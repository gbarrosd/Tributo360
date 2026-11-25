"""Fase 4: importação de NFS-e nacional, conferência pelo motor e DANFSe."""

import io
import zipfile
from decimal import Decimal

import httpx
import pytest
from django.urls import reverse

from documentos import nfe, nfse
from documentos.models import Divergencia, DocumentoFiscal

from .test_documentos import (
    CAMINHO_VALIDACAO,
    CNPJ_CLIENTE,
    CNPJ_EMPRESA,
    CNPJ_FORNECEDOR,
    EXEMPLOS,
    calculo_esperado,
    com_nome,
    enviar,
)
from .test_documentos import nfe_xml as nfe_xml_de_teste

D = Decimal
PDF = b"%PDF-1.7\n%falso\n"


def nfse_xml(numero, base="1000.00", prestador=CNPJ_EMPRESA, tomador=CNPJ_CLIENTE, op_simp_nac="1",
             c_class_trib="000001", cst="000", **valores):  # fmt: skip
    """NFS-e nacional mínima (leiaute 1.01) com o grupo IBSCBS; os valores padrão são os que o motor calcula."""
    esperado = calculo_esperado(D(base), c_class_trib)
    v = {chave: str(valores.get(chave, esperado[chave])) for chave in ("cbs", "ibs_uf", "ibs_mun", "ibs")}
    chave = f"431490221{prestador}000000000{numero:013d}123456789"[:50]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NFSe versao="1.01" xmlns="http://www.sped.fazenda.gov.br/nfse"><infNFSe Id="NFS{chave}">
  <nNFSe>{numero}</nNFSe><cLocIncid>4314902</cLocIncid><xTribNac>Consultoria</xTribNac>
  <emit><CNPJ>{prestador}</CNPJ><xNome>Prestador Exemplo</xNome></emit>
  <valores><vBC>{base}</vBC><vLiq>{base}</vLiq></valores>
  <IBSCBS><cLocalidadeIncid>4314902</cLocalidadeIncid>
    <valores><vBC>{base}</vBC>
      <uf><pIBSUF>0.10</pIBSUF></uf><mun><pIBSMun>0.00</pIBSMun></mun><fed><pCBS>0.90</pCBS></fed></valores>
    <totCIBS><vTotNF>{base}</vTotNF>
      <gIBS><vIBSTot>{v["ibs"]}</vIBSTot><gIBSUFTot><vIBSUF>{v["ibs_uf"]}</vIBSUF></gIBSUFTot>
        <gIBSMunTot><vIBSMun>{v["ibs_mun"]}</vIBSMun></gIBSMunTot></gIBS>
      <gCBS><vCBS>{v["cbs"]}</vCBS></gCBS></totCIBS></IBSCBS>
  <DPS versao="1.01"><infDPS Id="DPS1">
    <dhEmi>2026-06-10T10:00:00-03:00</dhEmi><serie>900</serie><nDPS>{numero}</nDPS>
    <prest><CNPJ>{prestador}</CNPJ><regTrib><opSimpNac>{op_simp_nac}</opSimpNac></regTrib></prest>
    <toma><CNPJ>{tomador}</CNPJ><xNome>Tomador Exemplo</xNome></toma>
    <serv><cServ><cTribNac>010101</cTribNac><xDescServ>Consultoria</xDescServ><cNBS>101011100</cNBS></cServ></serv>
    <valores><vServPrest><vServ>{base}</vServ></vServPrest></valores>
    <IBSCBS><valores><trib><gIBSCBS><CST>{cst}</CST><cClassTrib>{c_class_trib}</cClassTrib></gIBSCBS></trib></valores></IBSCBS>
  </infDPS></DPS>
</infNFSe></NFSe>""".encode()


@pytest.fixture
def danfse(motor):
    motor.rotas[("df", "POST", "/api/danfse")] = httpx.Response(200, content=PDF)
    return motor


# --- Leitura ----------------------------------------------------------------------------------------


def test_le_o_exemplo_oficial_de_nfse():
    nota = nfse.notas(nfse_raiz((EXEMPLOS / "nfse_valido_com_IBSCBS.xml").read_bytes()))[0]
    assert nota.modelo == "nfse" and nota.numero == "228" and nota.serie == "900"
    assert nota.chave == "41137002211111111000111000000000022826054848833960"
    assert nota.emitente_cnpj == "11111111000111" and nota.emitente_crt == "3"
    assert nota.destinatario_cnpj == "22222222000122"
    assert nota.municipio == "4113700" and nota.uf == "PR"
    item = nota.itens[0]
    assert (item.cst, item.c_class_trib, item.nbs) == ("000", "000001", "101011100")
    assert item.base_calculo == D("950.00") and item.cbs == D("8.55") and item.ibs_uf == D("0.95")
    assert item.aliquota_cbs == D("0.90") and item.ibs == D("0.95") and item.valor_produto == D("1000.00")


def nfse_raiz(conteudo):
    return nfe.analisar(conteudo)


def test_opsimpnac_vira_crt_equivalente():
    for op, crt in (("1", "3"), ("2", "4"), ("3", "1")):
        assert nfse.notas(nfse_raiz(nfse_xml(1, op_simp_nac=op)))[0].emitente_crt == crt


# --- Importação e conferência -------------------------------------------------------------------------


def test_importa_nfse_emitida_conforme(cenario, motor):
    resposta = enviar(cenario, nfse_xml(10))
    assert resposta.status_code == 302
    documento = DocumentoFiscal.objects.get()
    assert documento.modelo == DocumentoFiscal.Modelo.NFSE and documento.papel == DocumentoFiscal.Papel.EMITIDA
    assert documento.status == DocumentoFiscal.Status.CONFORME, documento.mensagens
    validacao = next(r for r in motor.recebidas if r.url.path == CAMINHO_VALIDACAO)
    assert validacao.url.params["tipo"] == "nfse"
    enviado = motor.json_da_ultima("rr")
    assert enviado["itens"][0]["nbs"] == "101011100" and enviado["municipio"] == 4314902
    pagina = cenario["cliente"].get(reverse("web:documento", args=[documento.pk])).content.decode()
    assert "NFS-e 10" in pagina and "NBS 101011100" in pagina and "Prestador" in pagina
    assert reverse("web:documento-danfse", args=[documento.pk]) in pagina


def test_nfse_recebida_com_cbs_errada_diverge(cenario, motor):
    enviar(cenario, nfse_xml(11, prestador=CNPJ_FORNECEDOR, tomador=CNPJ_EMPRESA, cbs="12.00"))
    documento = DocumentoFiscal.objects.get()
    assert documento.papel == DocumentoFiscal.Papel.RECEBIDA
    assert documento.status == DocumentoFiscal.Status.DIVERGENTE
    divergencia = Divergencia.objects.get()
    assert (divergencia.campo, divergencia.valor_nota, divergencia.valor_recalculado) == ("cbs", D("12.00"), D("9.00"))


def test_reenviar_a_mesma_nfse_nao_duplica(cenario, motor):
    enviar(cenario, nfse_xml(12))
    enviar(cenario, nfse_xml(12))
    assert DocumentoFiscal.objects.count() == 1


def test_prestador_do_simples_fica_sem_conferencia(cenario, motor):
    enviar(cenario, nfse_xml(13, op_simp_nac="3"))
    documento = DocumentoFiscal.objects.get()
    assert documento.status == DocumentoFiscal.Status.NAO_CONFERIDO
    assert "opSimpNac" in documento.mensagens[-1]["mensagem"]


def test_nfse_de_outra_empresa_e_recusada(cenario, motor):
    enviar(cenario, nfse_xml(14, prestador=CNPJ_FORNECEDOR, tomador=CNPJ_CLIENTE))
    assert not DocumentoFiscal.objects.exists()


def test_zip_com_nfe_e_nfse(cenario, motor):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_:
        zip_.writestr("nfse.xml", nfse_xml(15))
        zip_.writestr("nfe.xml", nfe_xml_de_teste(15, [{"base": D("100.00"), **_valores(D("100.00"))}]))
    url = reverse("web:documentos-importar", args=[cenario["empresa"].pk])
    cenario["cliente"].post(url, {"arquivos": [com_nome(buffer.getvalue(), "lote.zip")]})
    assert set(DocumentoFiscal.objects.values_list("modelo", flat=True)) == {"55", "nfse"}


def _valores(base):
    esperado = calculo_esperado(base)
    return {"cst": "000", "c_class_trib": "000001", **{k: esperado[k] for k in ("cbs", "ibs_uf", "ibs_mun")}}


# --- DANFSe ---------------------------------------------------------------------------------------------


def test_danfse_da_nfse_importada(cenario, danfse):
    enviar(cenario, nfse_xml(20))
    documento = DocumentoFiscal.objects.get()
    resposta = cenario["cliente"].get(reverse("web:documento-danfse", args=[documento.pk]) + "?situacao=2&canhoto=on")
    assert resposta.status_code == 200 and resposta["Content-Type"] == "application/pdf"
    assert resposta.content == PDF
    pedido = danfse.ultima("df")
    assert pedido.url.params["stc"] == "2" and pedido.url.params["canhoto"] == "true"
    assert pedido.content == documento.xml.encode()


def test_danfse_de_nfe_nao_existe(cenario, danfse):
    enviar(cenario, nfe_xml_de_teste(21, [{"base": D("100.00"), **_valores(D("100.00"))}]))
    documento = DocumentoFiscal.objects.get()
    resposta = cenario["cliente"].get(reverse("web:documento-danfse", args=[documento.pk]))
    assert resposta.status_code == 302 and danfse.ultima("df") is None


def test_danfse_avulso(cenario, danfse):
    url = reverse("web:danfse")
    assert cenario["cliente"].get(url).status_code == 200
    resposta = cenario["cliente"].post(url, {"arquivo": com_nome(nfse_xml(22), "n.xml"), "situacao": "0"})
    assert resposta.status_code == 200 and resposta.content == PDF
    assert not DocumentoFiscal.objects.exists()  # só gera o PDF, não importa


@pytest.mark.parametrize(
    "conteudo, trecho",
    [
        (None, "Este XML é uma NF-e (modelo 55)"),
        (b"<x>", "XML malformado"),
        (b"<x/>", "não é uma NFS-e nacional"),
        (b'<DPS xmlns="http://www.sped.fazenda.gov.br/nfse"/>', "é uma DPS"),
    ],
)
def test_danfse_avulso_recusa_o_que_nao_e_nfse_sem_chamar_o_gerador(cenario, danfse, conteudo, trecho):
    if conteudo is None:
        conteudo = nfe_xml_de_teste(30, [{"base": D("100.00"), **_valores(D("100.00"))}])
    resposta = cenario["cliente"].post(reverse("web:danfse"), {"arquivo": com_nome(conteudo, "n.xml"), "situacao": "0"})
    assert resposta.status_code == 200 and trecho in resposta.content.decode()
    assert danfse.ultima("df") is None


def test_danfse_avulso_recusado_mostra_a_mensagem(cenario, danfse):
    danfse.rotas[("df", "POST", "/api/danfse")] = httpx.Response(400, json={"mensagem": "EOF"})
    resposta = cenario["cliente"].post(
        reverse("web:danfse"), {"arquivo": com_nome(nfse_xml(23), "n.xml"), "situacao": "0"}, follow=True
    )
    assert resposta.status_code == 200 and "EOF" in resposta.content.decode()
