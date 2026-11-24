"""Catálogo de itens: validação da classificação, importação por planilha e aprovação.

A validação tem três camadas, da mais barata para a mais cara:

1. **formato**: NCM com 8 dígitos, NBS com 9, CST com 3, cClassTrib com 6, cAtivSN com 2;
2. **tabelas oficiais** (`ItemTabela`, copiadas dos dados abertos do motor): o código existe e o
   cClassTrib pertence ao CST. Só roda quando a tabela já foi sincronizada;
3. **motor do regime regular**: cada item vira um item de teste (base R$ 100,00) numa operação na
   data de hoje. Item que o motor não calcula vai para "revisar" com o erro do motor.

Qualquer erro manda o item para ``revisar``; sem erro, ele fica ``pendente`` até alguém aprovar.
"""

import csv
import io
import re
import unicodedata
import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from auditoria.servicos import registrar, snapshot
from engines import aliquotas, clientes
from engines.contratos import AliquotasNominais, ItemRegular, OperacaoRegular
from engines.http import ErroDeMotor
from engines.models import ItemTabela

from .models import ItemCatalogo

FORMATOS = {
    "ncm": (r"^\d{8}$", "NCM deve ter 8 dígitos."),
    "nbs": (r"^\d{9}$", "NBS deve ter 9 dígitos."),
    "cst": (r"^\d{3}$", "CST deve ter 3 dígitos."),
    "c_class_trib": (r"^\d{6}$", "cClassTrib deve ter 6 dígitos."),
    "c_ativ_sn": (r"^\d{2}$", "cAtivSN deve ter 2 dígitos."),
}
TABELAS = {"ncm": ItemTabela.Tabela.NCM, "nbs": ItemTabela.Tabela.NBS, "cst": ItemTabela.Tabela.CST}
MAX_ITENS_POR_CHAMADA = 500
MAX_LINHAS_PLANILHA = 5000
MAX_BYTES_PLANILHA = 5 * 1024 * 1024
BASE_TESTE = Decimal("100.00")
# A partir de 2027 o motor exige alíquotas. Para validar a classificação qualquer valor serve: o
# resultado do teste é descartado.
ALIQUOTAS_TESTE = AliquotasNominais(cbs=Decimal("8.8"), ibs_estadual=Decimal("0.1"), ibs_municipal=Decimal("0"))


def limpar_codigo(valor):
    """Tira pontos, traços e espaços de NCM/NBS/CST/cClassTrib vindos de planilha."""
    valor = "" if valor is None else str(valor).strip()
    if re.fullmatch(r"\d+\.0", valor):  # número lido do Excel como float
        valor = valor[:-2]
    return re.sub(r"[\s.\-/]", "", valor)


def _erro(origem, campo, mensagem, codigo=""):
    return {"origem": origem, "campo": campo, "mensagem": mensagem, "codigo": codigo}


def erros_de_formato(dados):
    erros = []
    tipo = dados.get("tipo")
    if tipo == ItemCatalogo.Tipo.PRODUTO and not dados.get("ncm"):
        erros.append(_erro("formato", "ncm", "Produto precisa de NCM."))
    if tipo == ItemCatalogo.Tipo.SERVICO and not dados.get("nbs"):
        erros.append(_erro("formato", "nbs", "Serviço precisa de NBS."))
    if not dados.get("c_class_trib"):
        erros.append(_erro("formato", "c_class_trib", "Informe o cClassTrib."))
    for campo, (padrao, mensagem) in FORMATOS.items():
        valor = dados.get(campo) or ""
        if valor and not re.fullmatch(padrao, valor):
            erros.append(_erro("formato", campo, mensagem))
    return erros


def _tabela_sincronizada(tabela, cache):
    if tabela not in cache:
        cache[tabela] = ItemTabela.objects.filter(tabela=tabela, vigente=True).exists()
    return cache[tabela]


def erros_de_tabela(item, cache=None):
    """Confere os códigos com as tabelas oficiais já copiadas. Tabela ainda não copiada não acusa erro."""
    cache = {} if cache is None else cache
    erros = []
    for campo, tabela in TABELAS.items():
        valor = getattr(item, campo)
        if valor and _tabela_sincronizada(tabela, cache):
            if not ItemTabela.objects.filter(tabela=tabela, vigente=True, codigo=valor).exists():
                erros.append(_erro("tabela", campo, f"{campo.upper()} {valor} não consta na tabela oficial vigente."))
    if item.c_class_trib and _tabela_sincronizada(ItemTabela.Tabela.CCLASSTRIB, cache):
        if not ItemTabela.objects.filter(
            tabela=ItemTabela.Tabela.CCLASSTRIB, vigente=True, codigo=item.c_class_trib
        ).exists():
            erros.append(
                _erro("tabela", "c_class_trib", f"cClassTrib {item.c_class_trib} não consta na tabela oficial.")
            )
        elif item.cst and _tabela_sincronizada(ItemTabela.Tabela.CST, cache):
            cst = ItemTabela.objects.filter(tabela=ItemTabela.Tabela.CST, vigente=True, codigo=item.cst).first()
            permitidos = (cst.dados or {}).get("cClassTrib") if cst else None
            if permitidos and item.c_class_trib not in permitidos:
                erros.append(
                    _erro("tabela", "c_class_trib", f"cClassTrib {item.c_class_trib} não pertence ao CST {item.cst}.")
                )
    return erros


def _validar_no_motor(itens):
    """Manda os itens ao motor do regime regular. Devolve {item.pk: [erros]} e a versão usada.

    Itens sem CST não vão ao motor (o motor exige CST); ficam só com as validações locais.
    """
    hoje = timezone.localtime()
    exige = aliquotas.exige_aliquotas(hoje.date())
    erros, versao = {}, ""
    for inicio in range(0, len(itens), MAX_ITENS_POR_CHAMADA):
        lote = itens[inicio : inicio + MAX_ITENS_POR_CHAMADA]
        empresa = lote[0].empresa
        operacao = OperacaoRegular(
            id=uuid.uuid4().hex,
            fato_gerador=hoje.replace(microsecond=0),
            uf=empresa.uf,
            municipio=int(empresa.municipio_ibge),
            itens=tuple(
                ItemRegular(
                    numero=numero,
                    cst=item.cst,
                    c_class_trib=item.c_class_trib,
                    base_calculo=BASE_TESTE,
                    ncm=item.ncm,
                    nbs=item.nbs,
                    aliquotas=ALIQUOTAS_TESTE if exige else None,
                )
                for numero, item in enumerate(lote, start=1)
            ),
        )
        resultado = clientes.regime_regular().calcular(operacao, contexto={"validacao_catalogo": True})
        versao = f"{resultado.versao.motor} {resultado.versao.versao_app} / {resultado.versao.versao_db}".strip()
        por_numero = {r.numero: r for r in resultado.itens}
        for numero, item in enumerate(lote, start=1):
            calculado = por_numero.get(numero)
            if calculado is None:
                erros[item.pk] = [_erro("motor", "", "O motor não devolveu este item.")]
            elif not calculado.estado.calculou:
                erros[item.pk] = [
                    _erro("motor", _campo_do_ponteiro(e.campo), e.detalhe or e.titulo, e.codigo)
                    for e in calculado.erros
                ] or [_erro("motor", "", f"O motor não calculou o item ({calculado.estado}).")]
    return erros, versao


CAMPOS_MOTOR = {"ncm": "ncm", "nbs": "nbs", "cst": "cst", "cClassTrib": "c_class_trib"}


def _campo_do_ponteiro(ponteiro):
    """'#/itens/3/ncm' → 'ncm'."""
    ultimo = (ponteiro or "").rstrip("/").split("/")[-1]
    return CAMPOS_MOTOR.get(ultimo, ultimo)


def validar(itens):
    """Valida e grava o status de cada item. Devolve um resumo para a tela.

    Não muda itens aprovados que continuam sem erro (a aprovação vale até a classificação mudar).
    """
    itens = list(itens)
    if not itens:
        return {"validados": 0, "revisar": 0, "motor": "", "motor_indisponivel": False}
    cache, erros_locais = {}, {}
    for item in itens:
        erros_locais[item.pk] = erros_de_formato(item.classificacao) or erros_de_tabela(item, cache)

    para_motor = [i for i in itens if not erros_locais[i.pk] and i.cst]
    erros_motor, versao, indisponivel = {}, "", False
    if para_motor:
        try:
            erros_motor, versao = _validar_no_motor(para_motor)
        except ErroDeMotor as exc:
            indisponivel = str(exc.mensagem or exc)

    agora = timezone.now()
    revisar = 0
    for item in itens:
        erros = erros_locais[item.pk] + erros_motor.get(item.pk, [])
        avisos = []
        if not item.cst:
            avisos.append("Sem CST: o item não foi conferido pelo motor do regime regular.")
        elif indisponivel and not erros_locais[item.pk]:
            avisos.append(f"Motor indisponível, o item não foi conferido pelo motor: {indisponivel}")
        item.validacao = {"erros": erros, "avisos": avisos, "motor": versao if item in para_motor else ""}
        item.validado_em = agora
        item.atualizado_em = agora
        if erros:
            revisar += 1
            item.status_classificacao = ItemCatalogo.Status.REVISAR
            item.aprovado_por = None
            item.aprovado_em = None
        elif item.status_classificacao == ItemCatalogo.Status.REVISAR:
            item.status_classificacao = ItemCatalogo.Status.PENDENTE
    ItemCatalogo.objects.bulk_update(
        itens, ["validacao", "validado_em", "status_classificacao", "aprovado_por", "aprovado_em", "atualizado_em"]
    )
    return {"validados": len(itens), "revisar": revisar, "motor": versao, "motor_indisponivel": bool(indisponivel)}


# --- Cadastro -----------------------------------------------------------------------------------


def _normalizar(dados):
    dados = dict(dados)
    for campo in FORMATOS:
        if campo in dados:
            dados[campo] = limpar_codigo(dados[campo])
    return dados


@transaction.atomic
def criar_item(request, empresa, dados):
    item = ItemCatalogo(escritorio=request.membership.escritorio, empresa=empresa, **_normalizar(dados))
    item.save()
    validar([item])
    registrar(request, "item_catalogo.criar", item, depois=snapshot(item))
    return item


@transaction.atomic
def atualizar_item(request, item, dados):
    antes = snapshot(item)
    dados = _normalizar(dados)
    mudou_classificacao = any(
        campo in dados and dados[campo] != getattr(item, campo) for campo in ItemCatalogo.CAMPOS_CLASSIFICACAO
    )
    for campo, valor in dados.items():
        setattr(item, campo, valor)
    if mudou_classificacao:
        item.status_classificacao = ItemCatalogo.Status.PENDENTE
        item.aprovado_por = None
        item.aprovado_em = None
    item.save()
    if mudou_classificacao:
        validar([item])
    registrar(request, "item_catalogo.editar", item, antes=antes, depois=snapshot(item))
    return item


@transaction.atomic
def aprovar_itens(request, itens):
    """Aprova itens sem erro. Itens em "revisar" não são aprovados (precisam ser corrigidos antes)."""
    aprovados, recusados = [], []
    agora = timezone.now()
    for item in itens:
        if item.status_classificacao == ItemCatalogo.Status.REVISAR:
            recusados.append(item)
            continue
        if item.status_classificacao == ItemCatalogo.Status.APROVADO:
            continue
        antes = snapshot(item)
        item.status_classificacao = ItemCatalogo.Status.APROVADO
        item.aprovado_por = request.user
        item.aprovado_em = agora
        item.save(update_fields=["status_classificacao", "aprovado_por", "aprovado_em", "atualizado_em"])
        registrar(request, "item_catalogo.aprovar", item, antes=antes, depois=snapshot(item))
        aprovados.append(item)
    return aprovados, recusados


# --- Importação por planilha ------------------------------------------------------------------

# Cabeçalho aceito (sem acento, minúsculo) → campo do modelo.
COLUNAS = {
    "tipo": "tipo",
    "codigo": "codigo",
    "codigo interno": "codigo",
    "descricao": "descricao",
    "ncm": "ncm",
    "nbs": "nbs",
    "cst": "cst",
    "cclasstrib": "c_class_trib",
    "c_class_trib": "c_class_trib",
    "cativsn": "c_ativ_sn",
    "c_ativ_sn": "c_ativ_sn",
}
COLUNAS_MODELO = ["tipo", "codigo", "descricao", "ncm", "nbs", "cst", "cclasstrib", "cativsn"]
TIPOS = {"produto": "produto", "p": "produto", "mercadoria": "produto", "servico": "servico", "s": "servico"}


def _sem_acento(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in texto if not unicodedata.combining(c)).strip().lower()


def _linhas_csv(conteudo):
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            texto = conteudo.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    amostra = texto[:4096]
    delimitador = ";" if amostra.count(";") >= amostra.count(",") else ","
    return list(csv.reader(io.StringIO(texto), delimiter=delimitador))


def _linhas_xlsx(conteudo):
    from openpyxl import load_workbook

    try:
        planilha = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except Exception as exc:  # arquivo corrompido ou que não é XLSX
        raise ValidationError({"arquivo": "Não consegui abrir a planilha XLSX."}) from exc
    aba = planilha.worksheets[0]
    linhas = []
    for linha in aba.iter_rows(values_only=True):
        linhas.append(["" if v is None else str(v) for v in linha])
        if len(linhas) > MAX_LINHAS_PLANILHA + 1:
            break
    planilha.close()
    return linhas


def ler_planilha(nome, conteudo):
    """Lê CSV (vírgula ou ponto e vírgula) ou XLSX. Devolve lista de (número da linha, dict)."""
    if len(conteudo) > MAX_BYTES_PLANILHA:
        raise ValidationError({"arquivo": "A planilha passa de 5 MB."})
    nome = (nome or "").lower()
    if nome.endswith(".xlsx"):
        linhas = _linhas_xlsx(conteudo)
    elif nome.endswith((".csv", ".txt")):
        linhas = _linhas_csv(conteudo)
    else:
        raise ValidationError({"arquivo": "Envie um arquivo .csv ou .xlsx."})
    linhas = [linha for linha in linhas if any(str(c).strip() for c in linha)]
    if not linhas:
        raise ValidationError({"arquivo": "A planilha está vazia."})
    cabecalho = [COLUNAS.get(_sem_acento(c)) for c in linhas[0]]
    faltando = {"codigo", "descricao", "c_class_trib"} - set(cabecalho)
    if faltando:
        raise ValidationError({"arquivo": "Faltam colunas na primeira linha: " + ", ".join(sorted(faltando)) + "."})
    if len(linhas) - 1 > MAX_LINHAS_PLANILHA:
        raise ValidationError({"arquivo": f"A planilha passa de {MAX_LINHAS_PLANILHA} linhas."})
    resultado = []
    for numero, linha in enumerate(linhas[1:], start=2):
        dados = {}
        for campo, valor in zip(cabecalho, linha, strict=False):
            if campo:
                dados[campo] = str(valor).strip()
        resultado.append((numero, dados))
    return resultado


def _dados_da_linha(dados):
    """Normaliza uma linha. Devolve (dados, erro_que_impede_importar)."""
    codigo = (dados.get("codigo") or "").strip()
    if re.fullmatch(r"\d+\.0", codigo):
        codigo = codigo[:-2]
    descricao = (dados.get("descricao") or "").strip()
    if not codigo:
        return None, "Sem código interno."
    if not descricao:
        return None, "Sem descrição."
    if len(codigo) > 60 or len(descricao) > 300:
        return None, "Código (até 60) ou descrição (até 300 caracteres) longos demais."
    normalizados = _normalizar({campo: dados.get(campo, "") for campo in FORMATOS})
    tipo = TIPOS.get(_sem_acento(dados.get("tipo")))
    if tipo is None:
        tipo = (
            ItemCatalogo.Tipo.SERVICO if normalizados["nbs"] and not normalizados["ncm"] else ItemCatalogo.Tipo.PRODUTO
        )
    # Campo maior que a coluna do banco: guarda cortado e deixa o erro de formato apontar.
    limites = {"ncm": 8, "nbs": 9, "cst": 3, "c_class_trib": 6, "c_ativ_sn": 2}
    erros_tamanho = [c for c, limite in limites.items() if len(normalizados[c]) > limite]
    if erros_tamanho:
        return None, "Código grande demais em: " + ", ".join(erros_tamanho) + "."
    return {"codigo": codigo, "descricao": descricao, "tipo": tipo, **normalizados}, None


@transaction.atomic
def importar_planilha(request, empresa, nome, conteudo):
    """Cria ou atualiza itens pelo código interno e valida tudo que mudou.

    Linha sem código ou descrição é ignorada e listada no resumo. Linha com classificação errada é
    importada e vai para "revisar", com o erro, para ser corrigida na tela.
    """
    linhas = ler_planilha(nome, conteudo)
    existentes = {i.codigo: i for i in ItemCatalogo.objects.filter(empresa=empresa)}
    criados, atualizados, iguais, ignoradas = [], [], 0, []
    vistos = set()
    for numero, bruto in linhas:
        dados, erro = _dados_da_linha(bruto)
        if erro:
            ignoradas.append({"linha": numero, "erro": erro})
            continue
        if dados["codigo"] in vistos:
            ignoradas.append({"linha": numero, "erro": f"Código {dados['codigo']} repetido na planilha."})
            continue
        vistos.add(dados["codigo"])
        item = existentes.get(dados["codigo"])
        if item is None:
            criados.append(ItemCatalogo(escritorio=empresa.escritorio, empresa=empresa, **dados))
            continue
        mudou = {c: v for c, v in dados.items() if getattr(item, c) != v}
        if not mudou:
            iguais += 1
            continue
        if any(c in mudou for c in ItemCatalogo.CAMPOS_CLASSIFICACAO):
            item.status_classificacao = ItemCatalogo.Status.PENDENTE
            item.aprovado_por = None
            item.aprovado_em = None
        for campo, valor in mudou.items():
            setattr(item, campo, valor)
        item.ativo = True
        item.save()
        atualizados.append(item)

    ItemCatalogo.objects.bulk_create(criados, batch_size=500)
    validacao = validar(criados + atualizados)
    resumo = {
        "arquivo": nome,
        "linhas": len(linhas),
        "criados": len(criados),
        "atualizados": len(atualizados),
        "sem_mudanca": iguais,
        "ignoradas": ignoradas,
        "revisar": validacao["revisar"],
        "motor": validacao["motor"],
        "motor_indisponivel": validacao["motor_indisponivel"],
    }
    registrar(request, "item_catalogo.importar", empresa, depois=resumo)
    return resumo


def modelo_csv():
    """Planilha de exemplo para download."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(COLUNAS_MODELO)
    escritor.writerow(["produto", "ARROZ-5KG", "Arroz agulhinha tipo 1 5 kg", "10063021", "", "200", "200003", ""])
    escritor.writerow(["servico", "MENSALIDADE", "Mensalidade escolar", "", "122011200", "200", "200028", ""])
    return "﻿" + saida.getvalue()
