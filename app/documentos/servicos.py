"""Importação e conferência de documentos fiscais (Fase 4).

Recebe arquivos já lidos (nome, bytes), guarda cada nota uma única vez (chave de acesso e hash), valida o
XML no motor, recalcula pelo motor do regime regular e grava uma `Divergencia` para cada valor destacado
que difere do recalculado além da tolerância do escritório.
"""

import io
import zipfile
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from auditoria.servicos import registrar
from engines import aliquotas, clientes
from engines.http import ErroDeMotor
from simulacoes.servicos import simular_regime_regular

from . import nfe
from .models import Divergencia, DocumentoFiscal, LoteImportacao

MAX_BYTES_XML = 5 * 1024 * 1024
MAX_BYTES_ZIP = 50 * 1024 * 1024
MAX_ARQUIVOS_POR_LOTE = 200
MAX_BYTES_DESCOMPACTADOS = 200 * 1024 * 1024

CAMPOS_COMPARADOS = ("base_calculo", "cbs", "ibs_uf", "ibs_mun", "ibs", "imposto_seletivo")
ALIQUOTAS_DA_NOTA = {"cbs": "aliquota_cbs", "ibs_estadual": "aliquota_ibs_uf", "ibs_municipal": "aliquota_ibs_mun"}
CRT_REGIME_REGULAR = "3"


class LoteRecusado(Exception):
    """O envio inteiro não pôde ser lido (zip corrompido, grande demais, sem XML...)."""


# --- Arquivos ------------------------------------------------------------------------------------


def arquivos_do_envio(enviados):
    """[(nome, bytes)] dos XMLs de um envio: XMLs soltos e o conteúdo de .zip (sem pastas nem outros tipos)."""
    saida = []
    for nome, conteudo in enviados:
        if nome.lower().endswith(".zip"):
            saida.extend(_xmls_do_zip(nome, conteudo))
        else:
            saida.append((nome, conteudo))
        if len(saida) > MAX_ARQUIVOS_POR_LOTE:
            raise LoteRecusado(f"Envie no máximo {MAX_ARQUIVOS_POR_LOTE} XMLs por vez.")
    if not saida:
        raise LoteRecusado("Nenhum XML encontrado no envio.")
    return saida


def _xmls_do_zip(nome, conteudo):
    if len(conteudo) > MAX_BYTES_ZIP:
        raise LoteRecusado(f"{nome}: o .zip passa de {MAX_BYTES_ZIP // (1024 * 1024)} MB.")
    try:
        pacote = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile:
        raise LoteRecusado(f"{nome}: arquivo .zip corrompido ou inválido.") from None
    membros = [m for m in pacote.infolist() if not m.is_dir() and m.filename.lower().endswith(".xml")]
    if len(membros) > MAX_ARQUIVOS_POR_LOTE:
        raise LoteRecusado(f"{nome}: o .zip tem {len(membros)} XMLs; envie no máximo {MAX_ARQUIVOS_POR_LOTE} por vez.")
    if sum(m.file_size for m in membros) > MAX_BYTES_DESCOMPACTADOS:
        raise LoteRecusado(f"{nome}: o conteúdo descompactado é grande demais.")
    saida = []
    for membro in membros:
        if membro.file_size > MAX_BYTES_XML:
            saida.append((membro.filename, None))  # vira "recusado" com mensagem de tamanho
            continue
        saida.append((membro.filename, pacote.read(membro)))
    return saida


# --- Importação ----------------------------------------------------------------------------------


def _papel(empresa, nota):
    if nota.emitente_cnpj == empresa.cnpj:
        return DocumentoFiscal.Papel.EMITIDA
    if nota.destinatario_cnpj == empresa.cnpj:
        return DocumentoFiscal.Papel.RECEBIDA
    return None


def _validar_no_motor(conteudo):
    """(validado, mensagem) — validado é None quando o motor não respondeu."""
    try:
        resultado = clientes.regime_regular().validar_xml(conteudo, tipo="nfe", subtipo="nota")
    except ErroDeMotor as erro:
        return None, {"etapa": "xsd", "mensagem": f"Validação do XML não feita: {erro}"}
    if resultado.valido:
        return True, None
    return False, {
        "etapa": "xsd",
        "mensagem": resultado.mensagem,
        "linha": resultado.linha,
        "coluna": resultado.coluna,
    }


def importar(request, empresa, enviados):
    """Importa e confere os XMLs de um envio. Devolve o `LoteImportacao` com o resultado de cada arquivo."""
    arquivos = arquivos_do_envio(enviados)
    escritorio = request.membership.escritorio
    lote = LoteImportacao.objects.create(
        escritorio=escritorio, empresa=empresa, enviado_por=request.user, arquivos=len(arquivos)
    )
    for nome, conteudo in arquivos:
        for resultado in _importar_arquivo(request, empresa, lote, nome, conteudo):
            lote.resultados.append(resultado)
            campo = {"novo": "novos", "duplicado": "duplicados"}.get(resultado["resultado"], "recusados")
            setattr(lote, campo, getattr(lote, campo) + 1)
    lote.save()
    registrar(
        request,
        "documentos.importar",
        lote,
        depois={
            "empresa": str(empresa.pk),
            "novos": lote.novos,
            "duplicados": lote.duplicados,
            "recusados": lote.recusados,
        },
    )
    return lote


def _recusado(nome, mensagem, linha=None, coluna=None):
    return {"arquivo": nome, "resultado": "recusado", "mensagem": mensagem, "linha": linha, "coluna": coluna}


def _importar_arquivo(request, empresa, lote, nome, conteudo):
    if conteudo is None or len(conteudo) > MAX_BYTES_XML:
        return [_recusado(nome, f"Arquivo maior que {MAX_BYTES_XML // (1024 * 1024)} MB.")]
    try:
        notas = nfe.ler(conteudo)
    except nfe.XmlInvalido as erro:
        return [_recusado(nome, erro.mensagem, erro.linha, erro.coluna)]

    resultados, pendentes = [], []
    for nota in notas:
        existente = (
            DocumentoFiscal.objects.do_escritorio(request.membership.escritorio)
            .filter(modelo=DocumentoFiscal.Modelo.NFE)
            .filter(chave=nota.chave)
            .first()
            or DocumentoFiscal.objects.do_escritorio(request.membership.escritorio).filter(hash_xml=nota.hash).first()
        )
        if existente is not None:
            resultados.append(
                {
                    "arquivo": nome,
                    "resultado": "duplicado",
                    "mensagem": f"NF-e {nota.numero}/{nota.serie} já importada em {existente.criado_em:%d/%m/%Y}.",
                    "documento_id": str(existente.pk) if existente.empresa_id == empresa.pk else None,
                }
            )
            continue
        papel = _papel(empresa, nota)
        if papel is None:
            resultados.append(
                _recusado(
                    nome,
                    f"NF-e {nota.numero}/{nota.serie}: nem o emitente ({nota.emitente_cnpj}) nem o destinatário "
                    f"({nota.destinatario_cnpj or '—'}) é o CNPJ desta empresa ({empresa.cnpj}).",
                )
            )
            continue
        pendentes.append((nota, papel))
    if not pendentes:
        return resultados

    validado, mensagem_xsd = _validar_no_motor(conteudo)
    if validado is False:
        resultados.extend(
            _recusado(
                nome, f"XML fora do leiaute: {mensagem_xsd['mensagem']}", mensagem_xsd["linha"], mensagem_xsd["coluna"]
            )
            for _ in pendentes
        )
        return resultados

    xml_texto = conteudo.decode("utf-8", errors="replace")
    for nota, papel in pendentes:
        try:
            with transaction.atomic():
                documento = DocumentoFiscal.objects.create(
                    escritorio=request.membership.escritorio,
                    empresa=empresa,
                    lote=lote,
                    modelo=DocumentoFiscal.Modelo.NFE,
                    papel=papel,
                    chave=nota.chave,
                    numero=nota.numero,
                    serie=nota.serie,
                    emissao=nota.emissao,
                    uf=nota.uf,
                    municipio=nota.municipio,
                    emitente_cnpj=nota.emitente_cnpj,
                    emitente_nome=nota.emitente_nome[:200],
                    emitente_crt=nota.emitente_crt,
                    destinatario_cnpj=nota.destinatario_cnpj,
                    destinatario_nome=nota.destinatario_nome[:200],
                    nome_arquivo=nome[:255],
                    hash_xml=nota.hash,
                    xml=xml_texto,
                    itens=[item.como_dict() for item in nota.itens],
                    totais=nota.totais,
                    validado_xsd=validado,
                    mensagens=[mensagem_xsd] if mensagem_xsd else [],
                    importado_por=request.user,
                )
        except IntegrityError:  # mesma nota enviada duas vezes no mesmo lote
            resultados.append(
                {"arquivo": nome, "resultado": "duplicado", "mensagem": f"NF-e {nota.numero} repetida no envio."}
            )
            continue
        conferir(request, documento, nota=nota)
        resultados.append(
            {
                "arquivo": nome,
                "resultado": "novo",
                "mensagem": f"NF-e {nota.numero}/{nota.serie}: {documento.get_status_display().lower()}.",
                "documento_id": str(documento.pk),
                "status": documento.status,
            }
        )
    return resultados


# --- Conferência ---------------------------------------------------------------------------------


def _item_da_nota(dados):
    return nfe.ItemNota(
        **{
            chave: (Decimal(valor) if chave in nfe.CAMPOS_DECIMAIS and valor not in (None, "") else valor)
            for chave, valor in dados.items()
        }
    )


def _dados_da_operacao(documento, itens):
    """Monta a entrada de `simular_regime_regular` a partir dos itens da nota.

    Alíquotas (a partir de 2027): as da tabela do Tributo360 para a data e o município da nota; o que a
    tabela não tiver vem da própria nota (e a conferência avisa). Em 2026 o motor usa as de teste.
    """
    data = documento.emissao.date()
    faltam_na_tabela = set()
    if aliquotas.exige_aliquotas(data):
        propostas = aliquotas.propostas_da_tabela(data, documento.municipio)
        faltam_na_tabela = {campo for campo, proposta in propostas.items() if proposta is None}
    entrada_itens = []
    for item in itens:
        dados_item = {
            "cst": item.cst,
            "c_class_trib": item.c_class_trib,
            "base_calculo": item.base_calculo,
            "ncm": item.ncm,
            "quantidade": item.quantidade,
            "unidade": item.unidade,
        }
        nota_aliquotas = {
            campo: getattr(item, atributo)
            for campo, atributo in ALIQUOTAS_DA_NOTA.items()
            if campo in faltam_na_tabela and getattr(item, atributo) is not None
        }
        if nota_aliquotas:
            dados_item["aliquotas"] = nota_aliquotas
        if item.seletivo.get("cst"):
            dados_item["imposto_seletivo"] = {
                "cst": item.seletivo["cst"],
                "c_class_trib": item.seletivo["c_class_trib"],
                "base_calculo": Decimal(item.seletivo["base_calculo"] or "0"),
                "quantidade": Decimal(item.seletivo["quantidade"]) if item.seletivo.get("quantidade") else None,
                "unidade": item.seletivo.get("unidade", ""),
            }
        entrada_itens.append(dados_item)
    dados = {
        "empresa": documento.empresa,
        "data_fato_gerador": documento.emissao,
        "uf": documento.uf,
        "municipio": documento.municipio,
        "rotulo": f"Conferência NF-e {documento.numero}/{documento.serie}",
        "itens": entrada_itens,
    }
    aviso = ""
    if faltam_na_tabela:
        nomes = ", ".join(sorted(aliquotas_nome(c) for c in faltam_na_tabela))
        aviso = f"Sem alíquota de {nomes} na tabela do Tributo360 para esta data e local: usei a da própria nota."
    return dados, aviso


def aliquotas_nome(campo):
    return {"cbs": "CBS", "ibs_estadual": "IBS estadual", "ibs_municipal": "IBS municipal"}[campo]


def _diferenca(nota, recalculado, tolerancia):
    if nota is None:
        return None
    recalculado = Decimal(str(recalculado)) if recalculado not in (None, "") else Decimal("0")
    diferenca = nota - recalculado
    return diferenca if abs(diferenca) > tolerancia else None


def conferir(request, documento, nota=None):
    """(Re)confere o documento: recalcula pelo motor e regrava as divergências abertas."""
    itens = [_item_da_nota(dados) for dados in documento.itens]
    tolerancia = request.membership.escritorio.tolerancia_divergencia
    mensagens = [m for m in documento.mensagens if m.get("etapa") == "xsd"]
    documento.tolerancia = tolerancia
    documento.conferido_em = timezone.now()

    def concluir(status, novas=None, simulacao=None):
        with transaction.atomic():
            documento.divergencias.filter(status=Divergencia.Status.ABERTA).delete()
            # A mesma diferença já justificada ou resolvida não volta a abrir numa nova conferência.
            tratadas = {_assinatura(d) for d in documento.divergencias.all()}
            novas = [d for d in (novas or []) if _assinatura(d) not in tratadas]
            for divergencia in novas:
                divergencia.escritorio = documento.escritorio
                divergencia.documento = documento
            Divergencia.objects.bulk_create(novas)
            documento.status = status
            documento.mensagens = mensagens
            documento.simulacao = simulacao
            documento.save()
        return documento

    if documento.emitente_crt != CRT_REGIME_REGULAR:
        mensagens.append(
            {
                "etapa": "recalculo",
                "mensagem": "Emitente do Simples Nacional (CRT "
                f"{documento.emitente_crt or '?'}): a conferência pelo motor do Simples entra numa próxima etapa.",
            }
        )
        return concluir(DocumentoFiscal.Status.NAO_CONFERIDO)
    sem_grupo = [item.numero for item in itens if not item.tem_ibscbs]
    if sem_grupo:
        mensagens.append(
            {
                "etapa": "recalculo",
                "mensagem": "A nota não destaca CBS/IBS (grupo IBSCBS) no(s) item(ns) "
                + ", ".join(map(str, sem_grupo))
                + ". Sem classificação não há o que conferir.",
            }
        )
        return concluir(DocumentoFiscal.Status.NAO_CONFERIDO)

    dados, aviso = _dados_da_operacao(documento, itens)
    if aviso:
        mensagens.append({"etapa": "recalculo", "mensagem": aviso})
    entrada = {"origem": "documento_fiscal", "documento_id": str(documento.pk), "chave": documento.chave}
    try:
        simulacao = simular_regime_regular(request, dados, entrada)
    except serializers.ValidationError as erro:
        mensagens.append({"etapa": "recalculo", "mensagem": _texto_validacao(erro.detail)})
        return concluir(DocumentoFiscal.Status.ERRO)
    except ErroDeMotor as erro:
        mensagens.append({"etapa": "motor", "mensagem": f"O motor não recalculou a nota: {erro}"})
        return concluir(DocumentoFiscal.Status.ERRO)

    calculados = {item.numero: item for item in simulacao.itens.all()}
    divergencias = []
    for posicao, item in enumerate(itens, start=1):
        calculado = calculados.get(posicao)
        if calculado is None or calculado.erros or calculado.estado not in ("CALCULADO", "CALCULO_SIMULADO"):
            detalhes = "; ".join(
                f"{e.get('codigo', '')} {e.get('titulo', '')}".strip() for e in (calculado.erros if calculado else [])
            )
            divergencias.append(
                Divergencia(
                    numero_item=item.numero,
                    campo="calculo",
                    mensagem=f"O motor não calculou o item ({calculado.estado if calculado else 'sem resposta'})"
                    + (f": {detalhes}" if detalhes else "."),
                )
            )
            continue
        for campo in CAMPOS_COMPARADOS:
            valor_nota = getattr(item, campo)
            diferenca = _diferenca(valor_nota, calculado.valores.get(campo), tolerancia)
            if diferenca is not None:
                divergencias.append(
                    Divergencia(
                        numero_item=item.numero,
                        campo=campo,
                        valor_nota=valor_nota,
                        valor_recalculado=valor_nota - diferenca,
                        diferenca=diferenca,
                    )
                )
    status = DocumentoFiscal.Status.DIVERGENTE if divergencias else DocumentoFiscal.Status.CONFORME
    return concluir(status, divergencias, simulacao)


def _assinatura(divergencia):
    return (divergencia.numero_item, divergencia.campo, divergencia.valor_nota, divergencia.valor_recalculado)


def _texto_validacao(detalhe):
    if isinstance(detalhe, dict):
        return " ".join(_texto_validacao(v) for v in detalhe.values())
    if isinstance(detalhe, list):
        return " ".join(_texto_validacao(v) for v in detalhe)
    return str(detalhe)


# --- Tratamento das divergências -----------------------------------------------------------------


def tratar_divergencia(request, divergencia, status, comentario):
    antes = {"status": divergencia.status, "comentario": divergencia.comentario}
    divergencia.status = status
    divergencia.comentario = comentario
    if status == Divergencia.Status.ABERTA:
        divergencia.tratado_por, divergencia.tratado_em = None, None
    else:
        divergencia.tratado_por, divergencia.tratado_em = request.user, timezone.now()
    divergencia.save()
    registrar(
        request, "divergencia.tratar", divergencia, antes=antes, depois={"status": status, "comentario": comentario}
    )
    return divergencia


def salvar_tolerancia(request, valor):
    escritorio = request.membership.escritorio
    antes = {"tolerancia_divergencia": str(escritorio.tolerancia_divergencia)}
    escritorio.tolerancia_divergencia = valor
    escritorio.save(update_fields=["tolerancia_divergencia", "atualizado_em"])
    registrar(request, "escritorio.tolerancia", escritorio, antes=antes, depois={"tolerancia_divergencia": str(valor)})
