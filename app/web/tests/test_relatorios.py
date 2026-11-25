"""Relatórios em PDF e planilha (Fase 5): conteúdo, cabeçalhos HTTP, papel de leitura e isolamento."""

import io

import pytest
from django.test import Client
from django.urls import reverse
from openpyxl import load_workbook
from pypdf import PdfReader

from contas.papeis import Papel
from documentos.models import Divergencia, DocumentoFiscal
from simulacoes.models import Simulacao
from simulacoes.tests.test_simulacoes import MOTOR_RR, URL_RR, cadastrar_tabela_2027, corpo_2027

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def cenario(criar_escritorio, criar_empresa, cliente_de, motores_falsos):
    escritorio, dono, membership = criar_escritorio("Contábil Exemplo")
    empresa = criar_empresa(escritorio, razao_social="Mercado Bom Preço", uf="RS", municipio_ibge="4314902")
    navegador = Client()
    navegador.force_login(dono)
    return {
        "escritorio": escritorio,
        "membership": membership,
        "empresa": empresa,
        "api": cliente_de(membership),
        "navegador": navegador,
        "motores": motores_falsos,
    }


@pytest.fixture
def simulacao_2027(cenario):
    """Exemplo oficial de 4 itens (um com IS, um NBS e o item 4 com erro REG-011), alíquotas da tabela."""
    cadastrar_tabela_2027()
    cenario["motores"].gravada("rr", MOTOR_RR, "regime_regular_exemplo_oficial_2027.json")
    itens = [
        {"ncm": "10063021", "cst": "200", "c_class_trib": "200003", "base_calculo": "100.00"},
        {
            "ncm": "22030000",
            "cst": "000",
            "c_class_trib": "000001",
            "quantidade": "12",
            "unidade": "LT",
            "aliquotas": {"is_ad_valorem": "10"},
            "imposto_seletivo": {
                "cst": "000",
                "c_class_trib": "000001",
                "base_calculo": "150.00",
                "quantidade": "12",
                "unidade": "LT",
                "imposto_informado": "15.00",
            },
        },
        {"nbs": "122011200", "cst": "200", "c_class_trib": "200028", "base_calculo": "1200.00"},
        {"ncm": "02013000", "cst": "200", "c_class_trib": "200035", "base_calculo": "80.00"},
    ]
    resposta = cenario["api"].post(URL_RR, corpo_2027(cenario["empresa"], itens=itens), format="json")
    assert resposta.status_code == 201, resposta.content
    return Simulacao.objects.get(pk=resposta.json()["id"])


def _guardar_para_inspecao(conteudo, nome):
    """RELATORIOS_SAIDA=/pasta grava os arquivos gerados nos testes, para olhar o visual."""
    import os
    from pathlib import Path

    if pasta := os.environ.get("RELATORIOS_SAIDA"):
        Path(pasta, nome).write_bytes(conteudo)


def texto_do_pdf(conteudo):
    leitor = PdfReader(io.BytesIO(conteudo))
    texto = "\n".join(pagina.extract_text() for pagina in leitor.pages)
    return " ".join(texto.split()), len(leitor.pages)


def test_pdf_da_simulacao_tem_totais_premissas_memoria_e_versao(cenario, simulacao_2027):
    resposta = cenario["navegador"].get(reverse("web:simulacao-pdf", args=[simulacao_2027.pk]))
    assert resposta.status_code == 200
    assert resposta["Content-Type"] == "application/pdf"
    assert resposta["Content-Disposition"].startswith('attachment; filename="simulacao-mercado-bom-preco-2027-03-10')
    assert resposta["Cache-Control"] == "no-store"
    _guardar_para_inspecao(resposta.content, "simulacao.pdf")
    texto, paginas = texto_do_pdf(resposta.content)
    assert paginas >= 1
    assert "Cenário base" in texto and "Mercado Bom Preço" in texto
    assert "TOTAL DE TRIBUTOS" in texto
    assert "Resultado simulado" in texto  # aviso de simulação
    assert "tabela do Tributo360" in texto and "Fonte de teste" in texto  # origem das alíquotas
    assert "Memória de cálculo do motor".upper() in texto
    assert "A base de cálculo utilizada é de R$ 100,00" in texto  # memória do item 1
    assert "REG-011" in texto  # erro do item 4
    # Rodapé: quem gerou e versão do motor, em toda página.
    assert texto.count("Motor regime_regular 1.7.2") >= paginas
    assert f"Página 1 de {paginas}" in texto
    assert "Contábil Exemplo" in texto


def test_planilha_da_simulacao_tem_numeros_somaveis(cenario, simulacao_2027):
    resposta = cenario["navegador"].get(reverse("web:simulacao-xlsx", args=[simulacao_2027.pk]))
    assert resposta.status_code == 200 and resposta["Content-Type"] == XLSX
    _guardar_para_inspecao(resposta.content, "simulacao.xlsx")
    livro = load_workbook(io.BytesIO(resposta.content))
    assert livro.sheetnames == ["Resumo", "Itens", "Alíquotas", "Memória de cálculo"]

    resumo = {linha[0]: linha[1] for linha in livro["Resumo"].iter_rows(values_only=True) if linha and linha[0]}
    assert resumo["Total de tributos (R$)"] == float(
        simulacao_2027.total_cbs + simulacao_2027.total_ibs + simulacao_2027.total_imposto_seletivo
    )
    assert resumo["Resultado simulado"] == "Sim"
    assert "1.7.2" in resumo["Motor"]

    itens = list(livro["Itens"].iter_rows(values_only=True))
    cabecalho = itens[0]
    assert cabecalho[:5] == ("Item", "NCM", "NBS", "CST", "cClassTrib")
    linhas = itens[1:]
    assert len(linhas) == simulacao_2027.itens.count()
    coluna_cbs = cabecalho.index("CBS")
    assert all(isinstance(linha[coluna_cbs], int | float | None) for linha in linhas)
    soma_cbs = sum(linha[coluna_cbs] or 0 for linha in linhas)
    assert round(soma_cbs, 2) == float(simulacao_2027.total_cbs)

    aliquotas = list(livro["Alíquotas"].iter_rows(values_only=True))[1:]
    assert (1, "CBS", 8.8, "tabela do Tributo360") == aliquotas[0][:4]

    memoria = list(livro["Memória de cálculo"].iter_rows(values_only=True))[1:]
    assert any(linha[1] == "CBS" and "R$ 100,00" in linha[2] for linha in memoria)


def test_simulacao_do_simples_e_sem_memoria_tambem_gera(cenario, criar_empresa, criar_simulacao, versao_motor):
    simples = criar_empresa(cenario["escritorio"], razao_social="Padaria Simples", regime="simples", crt=1)
    simulacao = criar_simulacao(simples, tipo=Simulacao.Tipo.SIMPLES, rotulo="", simulado=True)
    simulacao.itens.create(
        escritorio=cenario["escritorio"],
        numero=1,
        estado="CALCULADO",
        ncm="22030000",
        c_class_trib="000001",
        c_ativ_sn="01",
        base_calculo="1000.00",
        valores={"base_calculo": "1000.00", "cbs": "5.20", "ibs": "1.30"},
        erros=[{"codigo": "REG-011", "titulo": "Classificação incompatível", "campo": "cClassTrib"}],
        origem_aliquotas={"origem": "informada_pelo_usuario", "pCBSSN": "0.52", "pIBSSN": "0.13"},
    )
    for nome in ("web:simulacao-pdf", "web:simulacao-xlsx"):
        resposta = cenario["navegador"].get(reverse(nome, args=[simulacao.pk]))
        assert resposta.status_code == 200, nome
    texto, _ = texto_do_pdf(cenario["navegador"].get(reverse("web:simulacao-pdf", args=[simulacao.pk])).content)
    assert "Simples Nacional" in texto and "O motor não devolveu memória de cálculo." in texto
    assert "informada só nesta simulação" in texto
    assert "REG-011 Classificação incompatível (cClassTrib)" in texto


def test_papel_leitura_baixa_relatorio(cenario, simulacao_2027, adicionar_membro):
    leitor = adicionar_membro(cenario["escritorio"], papel=Papel.LEITURA)
    navegador = Client()
    navegador.force_login(leitor.usuario)
    assert navegador.get(reverse("web:simulacao-pdf", args=[simulacao_2027.pk])).status_code == 200


def test_tela_do_resultado_tem_os_botoes_de_download(cenario, simulacao_2027):
    pagina = cenario["navegador"].get(reverse("web:simulacao", args=[simulacao_2027.pk])).content.decode()
    assert reverse("web:simulacao-pdf", args=[simulacao_2027.pk]) in pagina
    assert reverse("web:simulacao-xlsx", args=[simulacao_2027.pk]) in pagina


def test_membro_sem_acesso_a_empresa_nao_baixa(cenario, simulacao_2027, adicionar_membro):
    restrito = adicionar_membro(cenario["escritorio"], papel=Papel.CONTADOR, acesso_todas_empresas=False)
    navegador = Client()
    navegador.force_login(restrito.usuario)
    assert navegador.get(reverse("web:simulacao-pdf", args=[simulacao_2027.pk])).status_code == 404
    assert navegador.get(reverse("web:simulacao-xlsx", args=[simulacao_2027.pk])).status_code == 404


# --- Relatório de conferência ----------------------------------------------------------------------

URL_CONF = "web:relatorio-conferencia"


def criar_nota(empresa, numero, status, emissao="2026-11-10T10:00:00-03:00", **extra):
    from django.utils.dateparse import parse_datetime

    dados = {
        "escritorio": empresa.escritorio,
        "empresa": empresa,
        "modelo": DocumentoFiscal.Modelo.NFE,
        "papel": DocumentoFiscal.Papel.EMITIDA,
        "chave": f"{numero:044d}",
        "numero": str(numero),
        "serie": "1",
        "emissao": parse_datetime(emissao),
        "emitente_cnpj": empresa.cnpj,
        "emitente_nome": empresa.razao_social,
        "destinatario_cnpj": "11222333000181",
        "destinatario_nome": "Cliente Atacadista",
        "hash_xml": f"{numero:064d}",
        "xml": "<NFe/>",
        "status": status,
        "totais": {"valor_nota": "1100.00", "base_calculo": "1000.00", "cbs": "9.00", "ibs": "1.00"},
    }
    dados.update(extra)
    return DocumentoFiscal.objects.create(**dados)


def divergir(documento, campo="cbs", status=Divergencia.Status.ABERTA, **extra):
    dados = {"numero_item": 1, "valor_nota": "9.00", "valor_recalculado": "8.80", "diferenca": "0.20"}
    dados.update(extra)
    return Divergencia.objects.create(
        escritorio=documento.escritorio, documento=documento, campo=campo, status=status, **dados
    )


@pytest.fixture
def carteira(cenario, criar_empresa, criar_escritorio):
    mercado = cenario["empresa"]
    padaria = criar_empresa(cenario["escritorio"], razao_social="Padaria Pão Quente")
    ok = criar_nota(mercado, 101, DocumentoFiscal.Status.CONFORME)
    ruim = criar_nota(mercado, 102, DocumentoFiscal.Status.DIVERGENTE)
    divergir(ruim, "cbs", diferenca="-0.20")
    divergir(ruim, "ibs", status=Divergencia.Status.JUSTIFICADA, comentario="Redução aplicada pelo cliente")
    outra = criar_nota(padaria, 201, DocumentoFiscal.Status.DIVERGENTE, papel=DocumentoFiscal.Papel.RECEBIDA)
    divergir(outra, "base_calculo", valor_nota="100.00", valor_recalculado="90.00", diferenca="10.00")
    criar_nota(mercado, 103, DocumentoFiscal.Status.CONFORME, emissao="2026-09-01T10:00:00-03:00")  # fora
    # Escritório B, mesmo período: nunca aparece.
    escritorio_b, _, _ = criar_escritorio("Escritório B")
    empresa_b = criar_empresa(escritorio_b, razao_social="Empresa Alheia SA")
    divergir(criar_nota(empresa_b, 901, DocumentoFiscal.Status.DIVERGENTE), "cbs", diferenca="999.00")
    return {"mercado": mercado, "padaria": padaria, "empresa_b": empresa_b, "ok": ok, "ruim": ruim}


PERIODO = {"de": "2026-11-01", "ate": "2026-11-30"}


def test_tela_do_relatorio_abre_com_o_mes_corrente(cenario, carteira):
    resposta = cenario["navegador"].get(reverse(URL_CONF) + f"?empresa={carteira['mercado'].pk}")
    pagina = resposta.content.decode()
    assert resposta.status_code == 200
    assert reverse("web:relatorio-conferencia-pdf") in pagina and reverse("web:relatorio-conferencia-xlsx") in pagina
    assert f'value="{carteira["mercado"].pk}" selected' in pagina
    assert "Empresa Alheia" not in pagina
    assert reverse(URL_CONF) in cenario["navegador"].get(reverse("web:documentos")).content.decode()


def test_pdf_da_carteira_tem_quadro_por_empresa_e_so_divergencias_abertas(cenario, carteira):
    resposta = cenario["navegador"].get(reverse("web:relatorio-conferencia-pdf"), PERIODO)
    assert resposta.status_code == 200 and resposta["Content-Type"] == "application/pdf"
    assert 'filename="conferencia-todas-as-empresas-2026-11-01-a-2026-11-30.pdf"' in resposta["Content-Disposition"]
    _guardar_para_inspecao(resposta.content, "conferencia.pdf")
    texto, _ = texto_do_pdf(resposta.content)
    assert "Mercado Bom Preço" in texto and "Padaria Pão Quente" in texto
    assert "Por empresa" in texto and "Divergências abertas" in texto
    assert "102/1" in texto and "201/1" in texto and "101/1" in texto
    assert "103/1" not in texto  # emitida fora do período
    assert "Redução aplicada" not in texto  # justificada fica de fora no modo "só as abertas"
    assert "-0,20" in texto and "+10,00" in texto
    assert "R$ 10,20" in texto  # diferença absoluta em aberto: 0,20 + 10,00
    assert "Empresa Alheia" not in texto and "999" not in texto
    assert "tolerância R$" in texto


def test_planilha_de_uma_empresa_com_todas_as_divergencias(cenario, carteira):
    parametros = {**PERIODO, "empresa": carteira["mercado"].pk, "divergencias": "todas"}
    resposta = cenario["navegador"].get(reverse("web:relatorio-conferencia-xlsx"), parametros)
    assert resposta.status_code == 200 and resposta["Content-Type"] == XLSX
    assert "conferencia-mercado-bom-preco-2026-11-01" in resposta["Content-Disposition"]
    _guardar_para_inspecao(resposta.content, "conferencia.xlsx")
    livro = load_workbook(io.BytesIO(resposta.content))
    assert livro.sheetnames == ["Resumo", "Por empresa", "Notas", "Divergências"]

    resumo = {linha[0]: linha[1] for linha in livro["Resumo"].iter_rows(values_only=True) if linha and linha[0]}
    assert resumo["Notas no período"] == 2 and resumo["Conformes"] == 1 and resumo["Com divergência"] == 1
    assert resumo["Divergências abertas"] == 1
    assert resumo["Diferença em aberto (R$, soma absoluta)"] == 0.2

    notas = list(livro["Notas"].iter_rows(values_only=True))
    assert [linha[2] for linha in notas[1:]] == ["101", "102"]
    cab = notas[0]
    assert notas[1][cab.index("Valor da nota (R$)")] == 1100

    divergencias = list(livro["Divergências"].iter_rows(values_only=True))
    cab = divergencias[0]
    linhas = divergencias[1:]
    assert sorted(linha[cab.index("Situação")] for linha in linhas) == ["Aberta", "Justificada"]
    assert any(linha[cab.index("Comentário")] == "Redução aplicada pelo cliente" for linha in linhas)
    assert all(isinstance(linha[cab.index("Diferença (R$)")], float) for linha in linhas)


def test_relatorio_vazio_e_periodo_invalido(cenario, carteira):
    vazio = cenario["navegador"].get(
        reverse("web:relatorio-conferencia-pdf"), {"de": "2020-01-01", "ate": "2020-01-31"}
    )
    assert vazio.status_code == 200
    assert "Nenhuma nota importada no período." in texto_do_pdf(vazio.content)[0]
    invertido = cenario["navegador"].get(
        reverse("web:relatorio-conferencia-pdf"), {"de": "2026-11-30", "ate": "2026-11-01"}
    )
    assert invertido.status_code == 400 and "anterior à inicial" in invertido.content.decode()
    longo = cenario["navegador"].get(
        reverse("web:relatorio-conferencia-xlsx"), {"de": "2024-01-01", "ate": "2026-11-30"}
    )
    assert longo.status_code == 400


def test_empresa_de_outro_escritorio_no_filtro_e_recusada(cenario, carteira):
    for nome in ("web:relatorio-conferencia-pdf", "web:relatorio-conferencia-xlsx"):
        resposta = cenario["navegador"].get(reverse(nome), {**PERIODO, "empresa": carteira["empresa_b"].pk})
        assert resposta.status_code == 400, nome
        assert "Empresa Alheia" not in resposta.content.decode()


def test_membro_restrito_so_ve_as_empresas_dele(cenario, carteira, adicionar_membro):
    restrito = adicionar_membro(cenario["escritorio"], papel=Papel.LEITURA, acesso_todas_empresas=False)
    restrito.empresas_permitidas.add(carteira["padaria"])
    navegador = Client()
    navegador.force_login(restrito.usuario)
    texto, _ = texto_do_pdf(navegador.get(reverse("web:relatorio-conferencia-pdf"), PERIODO).content)
    assert "201/1" in texto and "Mercado Bom Preço" not in texto
    resposta = navegador.get(reverse("web:relatorio-conferencia-pdf"), {**PERIODO, "empresa": carteira["mercado"].pk})
    assert resposta.status_code == 400
