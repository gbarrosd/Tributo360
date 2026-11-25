# ruff: noqa: E501  (XMLs de exemplo em linha única)
"""Fase 4: importação de NF-e 55, conferência pelo motor e divergências.

O motor falso daqui calcula de verdade (base × alíquota, com redução por cClassTrib), para que as notas
de teste tenham divergências conhecidas: `test_vinte_notas_com_divergencias_conhecidas` é o aceite do
item 14 do planejamento.
"""

import io
import json
import zipfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx
import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from contas.papeis import Papel
from documentos import nfe
from documentos.models import Divergencia, DocumentoFiscal, LoteImportacao

CNPJ_EMPRESA = "11222333000181"
CNPJ_CLIENTE = "22333444000172"
CNPJ_FORNECEDOR = "33444555000163"
CAMINHO_CALCULO = "/api/calculadora/observabilidade/regime-geral"
CAMINHO_VALIDACAO = "/api/calculadora/xml/validate"
EXEMPLO_OFICIAL = (
    Path(settings.BASE_DIR).parent
    / "engines/calculadora-rtc-regime-regular/src/test/resources/xml/nfe/valido/nota/nfe_valido.xml"
)

# Alíquotas de teste de 2026 aplicadas pelo motor e reduções por cClassTrib (simplificação do motor real).
ALIQUOTAS_2026 = {"cbs": Decimal("0.90"), "uf": Decimal("0.10"), "mun": Decimal("0.00")}
REDUCOES = {"000001": Decimal("0"), "200034": Decimal("60")}
D = Decimal


def _q(valor):
    return valor.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def calculo_esperado(base, c_class_trib="000001", aliquotas=None):
    aliquotas = aliquotas or ALIQUOTAS_2026
    fator = (D("100") - REDUCOES.get(c_class_trib, D("0"))) / D("100")
    cbs = _q(base * aliquotas["cbs"] * fator / 100)
    uf = _q(base * aliquotas["uf"] * fator / 100)
    mun = _q(base * aliquotas["mun"] * fator / 100)
    return {"cbs": cbs, "ibs_uf": uf, "ibs_mun": mun, "ibs": uf + mun}


def motor_que_calcula(request):
    """Rota falsa do regime regular: calcula cada item como o motor faria (no que importa aqui)."""
    corpo = json.loads(request.content)
    objetos, total_cbs, total_ibs, total_base = [], D("0"), D("0"), D("0")
    for item in corpo["itens"]:
        if item["cClassTrib"] not in REDUCOES:
            objetos.append(
                {
                    "nObj": item["numero"],
                    "estadoItem": "INCONSISTENCIA_ENTRADA",
                    "erros": [{"code": "VAL-120", "title": "cClassTrib inexistente", "detail": item["cClassTrib"]}],
                }
            )
            continue
        nominais = item.get("aliquotasNominais")
        aliquotas = (
            {"cbs": D(nominais["cbs"]), "uf": D(nominais["ibsEstadual"]), "mun": D(nominais["ibsMunicipal"])}
            if nominais
            else None
        )
        base = D(item["baseCalculo"])
        valores = calculo_esperado(base, item["cClassTrib"], aliquotas)
        total_base += base
        total_cbs += valores["cbs"]
        total_ibs += valores["ibs"]
        objetos.append(
            {
                "nObj": item["numero"],
                "estadoItem": "CALCULADO",
                "tribCalc": {
                    "IBSCBS": {
                        "CST": item["cst"],
                        "cClassTrib": item["cClassTrib"],
                        "gIBSCBS": {
                            "vBC": str(base),
                            "gIBSUF": {"vIBSUF": str(valores["ibs_uf"])},
                            "gIBSMun": {"vIBSMun": str(valores["ibs_mun"])},
                            "vIBS": str(valores["ibs"]),
                            "gCBS": {"vCBS": str(valores["cbs"])},
                        },
                    }
                },
            }
        )
    total = {
        "tribCalc": {
            "IBSCBSTot": {
                "vBCIBSCBS": str(total_base),
                "gIBS": {"vIBS": str(total_ibs)},
                "gCBS": {"vCBS": str(total_cbs)},
            }
        }
    }
    return httpx.Response(
        200,
        json={"objetos": objetos, "total": total},
        headers={"X-CALC-APP-VERSION": "1.7.2", "X-CALC-DB-VERSION": "V0073"},
    )


def nfe_xml(numero, itens, emitente=CNPJ_EMPRESA, destinatario=CNPJ_CLIENTE, crt="3", emissao="2026-06-10T10:00:00-03:00",
            municipio="4314902", raiz="nfeProc"):  # fmt: skip
    """NF-e mínima no leiaute 4.00 com o grupo IBSCBS. `itens`: dicts com base, cbs, ibs_uf, ibs_mun (Decimal)."""
    chave = f"43260611222333000181550010000{numero:05d}1000{numero:05d}"[:44].ljust(44, "0")
    dets = []
    for n, item in enumerate(itens, start=1):
        ibs = item["ibs_uf"] + item["ibs_mun"]
        dets.append(f"""
      <det nItem="{n}"><prod><cProd>P{n:03d}</cProd><cEAN>SEM GTIN</cEAN><xProd>Produto {n}</xProd><NCM>{item.get("ncm", "22030000")}</NCM>
        <CFOP>5102</CFOP><uCom>UN</uCom><qCom>1.0000</qCom><vUnCom>{item["base"]}</vUnCom><vProd>{item["base"]}</vProd>
        <cEANTrib>SEM GTIN</cEANTrib><uTrib>UN</uTrib><qTrib>1.0000</qTrib><vUnTrib>{item["base"]}</vUnTrib><indTot>1</indTot></prod>
        <imposto><IBSCBS><CST>{item.get("cst", "000")}</CST><cClassTrib>{item.get("c_class_trib", "000001")}</cClassTrib>
          <gIBSCBS><vBC>{item["base"]}</vBC>
            <gIBSUF><pIBSUF>0.10</pIBSUF><vIBSUF>{item["ibs_uf"]}</vIBSUF></gIBSUF>
            <gIBSMun><pIBSMun>0.00</pIBSMun><vIBSMun>{item["ibs_mun"]}</vIBSMun></gIBSMun>
            <vIBS>{ibs}</vIBS>
            <gCBS><pCBS>0.90</pCBS><vCBS>{item["cbs"]}</vCBS></gCBS>
          </gIBSCBS></IBSCBS></imposto></det>""")
    base = sum(i["base"] for i in itens)
    cbs = sum(i["cbs"] for i in itens)
    ibs = sum(i["ibs_uf"] + i["ibs_mun"] for i in itens)
    nfe_el = f"""<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe Id="NFe{chave}" versao="4.00">
      <ide><cUF>43</cUF><cNF>00000001</cNF><natOp>Venda</natOp><mod>55</mod><serie>1</serie><nNF>{numero}</nNF>
        <dhEmi>{emissao}</dhEmi><tpNF>1</tpNF><idDest>1</idDest><cMunFG>{municipio}</cMunFG><tpImp>1</tpImp><tpEmis>1</tpEmis>
        <cDV>0</cDV><tpAmb>2</tpAmb><finNFe>1</finNFe><indFinal>0</indFinal><indPres>1</indPres><procEmi>0</procEmi><verProc>1</verProc></ide>
      <emit><CNPJ>{emitente}</CNPJ><xNome>Emitente {emitente}</xNome><CRT>{crt}</CRT></emit>
      <dest><CNPJ>{destinatario}</CNPJ><xNome>Destinatario {destinatario}</xNome></dest>
      {"".join(dets)}
      <total><ICMSTot><vNF>{base}</vNF></ICMSTot>
        <IBSCBSTot><vBCIBSCBS>{base}</vBCIBSCBS><gIBS><vIBS>{ibs}</vIBS></gIBS><gCBS><vCBS>{cbs}</vCBS></gCBS></IBSCBSTot></total>
    </infNFe></NFe>"""
    if raiz == "NFe":
        return f'<?xml version="1.0" encoding="UTF-8"?>{nfe_el}'.encode()
    return (
        '<?xml version="1.0" encoding="UTF-8"?><nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
        f'{nfe_el}<protNFe versao="4.00"><infProt><chNFe>{chave}</chNFe><cStat>100</cStat></infProt></protNFe></nfeProc>'
    ).encode()


def item_certo(base, c_class_trib="000001", **ajustes):
    valores = calculo_esperado(D(base), c_class_trib)
    dados = {"base": D(base), "c_class_trib": c_class_trib, "cst": "200" if c_class_trib.startswith("2") else "000"}
    dados.update({"cbs": valores["cbs"], "ibs_uf": valores["ibs_uf"], "ibs_mun": valores["ibs_mun"]})
    dados.update(ajustes)
    return dados


@pytest.fixture
def motor(motores_falsos):
    motores_falsos.rotas[("rr", "POST", CAMINHO_CALCULO)] = motor_que_calcula
    motores_falsos.rotas[("rr", "POST", CAMINHO_VALIDACAO)] = httpx.Response(200, text="true")
    return motores_falsos


@pytest.fixture
def cenario(criar_escritorio, criar_empresa):
    escritorio, dono, _ = criar_escritorio("Contábil Exemplo")
    empresa = criar_empresa(
        escritorio, razao_social="Distribuidora Sul", cnpj=CNPJ_EMPRESA, uf="RS", municipio_ibge="4314902"
    )
    cliente = Client()
    cliente.force_login(dono)
    return {"escritorio": escritorio, "dono": dono, "empresa": empresa, "cliente": cliente}


def enviar(cenario, *arquivos):
    arquivos = [io.BytesIO(conteudo) for conteudo in arquivos]
    for posicao, arquivo in enumerate(arquivos, start=1):
        arquivo.name = getattr(arquivo, "name", None) or f"nota{posicao}.xml"
    url = reverse("web:documentos-importar", args=[cenario["empresa"].pk])
    return cenario["cliente"].post(url, {"arquivos": arquivos})


def com_nome(conteudo, nome):
    arquivo = io.BytesIO(conteudo)
    arquivo.name = nome
    return arquivo


# --- Leitura do XML ---------------------------------------------------------------------------------


def test_le_o_exemplo_oficial_do_motor():
    nota = nfe.ler(EXEMPLO_OFICIAL.read_bytes())[0]
    assert nota.chave == "21250911111111000111558890000000051003360290"
    assert (nota.uf, nota.municipio, nota.emitente_crt) == ("MA", "2111300", "3")
    item = nota.itens[0]
    assert (item.cst, item.c_class_trib, item.base_calculo) == ("200", "200022", D("100.00"))
    assert (item.aliquota_ibs_uf, item.aliquota_cbs, item.cbs) == (D("10.00"), D("2.00"), D("0.00"))


def test_xml_malformado_informa_linha_e_coluna():
    with pytest.raises(nfe.XmlInvalido) as erro:
        nfe.ler(b'<?xml version="1.0"?>\n<NFe>\n  <infNFe>\n</NFe>')
    assert erro.value.linha == 4 and erro.value.coluna


def test_recusa_dtd_e_entidades():
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><NFe>&a;</NFe>'
    with pytest.raises(nfe.XmlInvalido, match="DTD"):
        nfe.ler(xml)


def test_recusa_nfce():
    xml = nfe_xml(1, [item_certo("100.00")]).replace(b"<mod>55</mod>", b"<mod>65</mod>")
    with pytest.raises(nfe.XmlInvalido, match="modelo 65"):
        nfe.ler(xml)


# --- Importação -------------------------------------------------------------------------------------


def test_importa_nota_conforme(cenario, motor):
    resposta = enviar(cenario, nfe_xml(1, [item_certo("1000.00"), item_certo("250.00", "200034")]))
    assert resposta.status_code == 302
    documento = DocumentoFiscal.objects.get()
    assert documento.status == DocumentoFiscal.Status.CONFORME, documento.mensagens
    assert documento.papel == DocumentoFiscal.Papel.EMITIDA and documento.validado_xsd is True
    assert documento.simulacao is not None and documento.simulacao.itens.count() == 2
    enviado = motor.json_da_ultima("rr")
    assert enviado["municipio"] == 4314902 and enviado["uf"] == "RS"
    assert enviado["itens"][1]["cClassTrib"] == "200034" and enviado["itens"][1]["baseCalculo"] == "250.00"
    pagina = cenario["cliente"].get(resposta["Location"]).content.decode()
    assert "Conforme" in pagina


def test_reenviar_o_mesmo_xml_nao_duplica(cenario, motor):
    xml = nfe_xml(7, [item_certo("100.00")])
    enviar(cenario, xml)
    enviar(cenario, xml)
    # Mesma nota em outra embalagem (NFe solta em vez de nfeProc): a chave de acesso pega.
    enviar(cenario, nfe_xml(7, [item_certo("100.00")], raiz="NFe"))
    assert DocumentoFiscal.objects.count() == 1
    ultimo = LoteImportacao.objects.order_by("-criado_em").first()
    assert (ultimo.novos, ultimo.duplicados) == (0, 1)


def test_xml_invalido_no_leiaute_mostra_linha_e_coluna(cenario, motor):
    motor.rotas[("rr", "POST", CAMINHO_VALIDACAO)] = httpx.Response(
        422, json={"detail": "Erro na linha 12, coluna 34: cvc-complex-type.2.4.a: elemento inesperado"}
    )
    resposta = enviar(cenario, nfe_xml(2, [item_certo("100.00")]))
    lote = LoteImportacao.objects.get()
    assert lote.recusados == 1 and not DocumentoFiscal.objects.exists()
    assert (lote.resultados[0]["linha"], lote.resultados[0]["coluna"]) == (12, 34)
    pagina = cenario["cliente"].get(resposta["Location"]).content.decode()
    assert "linha 12, coluna 34" in pagina


def test_xml_malformado_e_recusado_com_posicao(cenario, motor):
    enviar(cenario, b"<NFe><infNFe></NFe>")
    resultado = LoteImportacao.objects.get().resultados[0]
    assert resultado["resultado"] == "recusado" and resultado["linha"] == 1


def test_nota_de_outra_empresa_e_recusada(cenario, motor):
    enviar(cenario, nfe_xml(3, [item_certo("100.00")], emitente=CNPJ_FORNECEDOR, destinatario=CNPJ_CLIENTE))
    lote = LoteImportacao.objects.get()
    assert lote.recusados == 1 and "é o CNPJ desta empresa" in lote.resultados[0]["mensagem"]


def test_nota_recebida_de_fornecedor(cenario, motor):
    enviar(cenario, nfe_xml(4, [item_certo("80.00")], emitente=CNPJ_FORNECEDOR, destinatario=CNPJ_EMPRESA))
    assert DocumentoFiscal.objects.get().papel == DocumentoFiscal.Papel.RECEBIDA


def test_lote_zip_com_varias_notas(cenario, motor):
    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w") as zip_:
        for numero in range(10, 15):
            zip_.writestr(f"notas/{numero}.xml", nfe_xml(numero, [item_certo("100.00")]))
        zip_.writestr("leia-me.txt", "ignorado")
    resposta = cenario["cliente"].post(
        reverse("web:documentos-importar", args=[cenario["empresa"].pk]),
        {"arquivos": [com_nome(pacote.getvalue(), "notas.zip")]},
    )
    assert resposta.status_code == 302
    lote = LoteImportacao.objects.get()
    assert (lote.arquivos, lote.novos) == (5, 5)


def test_zip_corrompido_volta_para_o_formulario(cenario, motor):
    resposta = cenario["cliente"].post(
        reverse("web:documentos-importar", args=[cenario["empresa"].pk]),
        {"arquivos": [com_nome(b"isto nao e zip", "notas.zip")]},
    )
    assert resposta.status_code == 200 and "corrompido" in resposta.content.decode()


def test_emitente_do_simples_fica_sem_conferencia(cenario, motor):
    enviar(cenario, nfe_xml(5, [item_certo("100.00")], crt="1"))
    documento = DocumentoFiscal.objects.get()
    assert documento.status == DocumentoFiscal.Status.NAO_CONFERIDO
    assert motor.ultima("rr") is not None  # só validou o XML
    assert all("simples" not in r.url.path for r in motor.recebidas if r.method == "POST" and r.url.host == "rr")


def test_motor_fora_do_ar_marca_erro_e_permite_conferir_de_novo(cenario, motor):
    motor.fora_do_ar.add("rr")
    enviar(cenario, nfe_xml(6, [item_certo("100.00")]))
    documento = DocumentoFiscal.objects.get()
    assert documento.status == DocumentoFiscal.Status.ERRO and documento.validado_xsd is None
    motor.fora_do_ar.clear()
    from django.core.cache import cache

    cache.clear()  # fecha o circuito aberto pela queda
    resposta = cenario["cliente"].post(reverse("web:documento-conferir", args=[documento.pk]))
    assert resposta.status_code == 302
    documento.refresh_from_db()
    assert documento.status in (DocumentoFiscal.Status.CONFORME, DocumentoFiscal.Status.ERRO)


# --- Divergências -----------------------------------------------------------------------------------


def _casos_conhecidos():
    """20 notas: (itens, campos divergentes esperados como {(item, campo)})."""
    casos = []
    for n in range(1, 8):  # 7 notas certas, com 1 a 3 itens e redução
        itens = [item_certo(f"{100 * n}.00"), item_certo(f"{37 * n}.50", "200034")][: 1 + n % 2]
        casos.append((itens, set()))
    for n in range(8, 12):  # CBS a mais
        casos.append(([item_certo("1000.00", cbs=D("9.00") + n)], {(1, "cbs")}))
    for _ in range(12, 15):  # IBS estadual errado no 2º item
        itens = [item_certo("500.00"), item_certo("300.00", ibs_uf=D("0.90"))]
        casos.append((itens, {(2, "ibs_uf"), (2, "ibs")}))
    for _n in range(15, 17):  # redução não aplicada pelo emitente
        casos.append(
            (
                [item_certo("1000.00", "200034", cbs=D("9.00"), ibs_uf=D("1.00"))],
                {(1, "cbs"), (1, "ibs_uf"), (1, "ibs")},
            )
        )
    for _n in range(17, 19):  # diferença de 1 centavo: dentro da tolerância
        casos.append(([item_certo("1000.00", cbs=D("9.01"))], set()))
    casos.append(([item_certo("100.00", "999999")], {(1, "calculo")}))  # classificação que o motor recusa
    casos.append(([item_certo("100.00"), item_certo("100.00", cbs=D("0.00"))], {(2, "cbs")}))  # CBS não destacada
    assert len(casos) == 20
    return casos


def test_vinte_notas_com_divergencias_conhecidas(cenario, motor):
    casos = _casos_conhecidos()
    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w") as zip_:
        for numero, (itens, _) in enumerate(casos, start=1):
            zip_.writestr(f"{numero}.xml", nfe_xml(numero, itens))
    cenario["cliente"].post(
        reverse("web:documentos-importar", args=[cenario["empresa"].pk]),
        {"arquivos": [com_nome(pacote.getvalue(), "lote.zip")]},
    )
    assert DocumentoFiscal.objects.count() == 20
    for numero, (_, esperadas) in enumerate(casos, start=1):
        documento = DocumentoFiscal.objects.get(numero=str(numero))
        achadas = {(d.numero_item, d.campo) for d in documento.divergencias.all()}
        assert achadas == esperadas, (numero, documento.mensagens)
        esperado = DocumentoFiscal.Status.DIVERGENTE if esperadas else DocumentoFiscal.Status.CONFORME
        assert documento.status == esperado, numero


def test_tolerancia_do_escritorio(cenario, motor):
    cenario["escritorio"].tolerancia_divergencia = D("0.50")
    cenario["escritorio"].save()
    enviar(cenario, nfe_xml(30, [item_certo("1000.00", cbs=D("9.40"))]))
    assert DocumentoFiscal.objects.get().status == DocumentoFiscal.Status.CONFORME


def test_alterar_tolerancia_pela_tela(cenario):
    resposta = cenario["cliente"].post(reverse("web:tolerancia"), {"tolerancia": "0,05"})
    assert resposta.status_code == 302
    cenario["escritorio"].refresh_from_db()
    assert cenario["escritorio"].tolerancia_divergencia == D("0.05")


def test_justificar_divergencia_exige_comentario_e_nao_reabre(cenario, motor):
    enviar(cenario, nfe_xml(40, [item_certo("1000.00", cbs=D("12.00"))]))
    documento = DocumentoFiscal.objects.get()
    divergencia = documento.divergencias.get()
    assert (divergencia.valor_nota, divergencia.valor_recalculado, divergencia.diferenca) == (
        D("12.00"),
        D("9.00"),
        D("3.00"),
    )
    url = reverse("web:divergencia", args=[divergencia.pk])
    cenario["client" if False else "cliente"].post(url, {"status": "justificada", "comentario": ""})
    divergencia.refresh_from_db()
    assert divergencia.status == Divergencia.Status.ABERTA
    cenario["cliente"].post(url, {"status": "justificada", "comentario": "Cliente aplicou alíquota de 1,2% por erro."})
    divergencia.refresh_from_db()
    assert divergencia.status == Divergencia.Status.JUSTIFICADA and divergencia.tratado_por == cenario["dono"]
    cenario["cliente"].post(reverse("web:documento-conferir", args=[documento.pk]))
    assert documento.divergencias.count() == 1  # a mesma diferença justificada não volta aberta


def test_assistente_nao_trata_divergencia(cenario, motor, adicionar_membro):
    enviar(cenario, nfe_xml(41, [item_certo("1000.00", cbs=D("12.00"))]))
    assistente = adicionar_membro(cenario["escritorio"], papel=Papel.ASSISTENTE).usuario
    cliente = Client()
    cliente.force_login(assistente)
    divergencia = Divergencia.objects.get()
    resposta = cliente.post(reverse("web:divergencia", args=[divergencia.pk]), {"status": "resolvida"})
    assert resposta.status_code == 403


def test_telas_de_documentos_abrem(cenario, motor):
    enviar(cenario, nfe_xml(50, [item_certo("1000.00", cbs=D("12.00")), item_certo("10.00", "999999")]))
    documento = DocumentoFiscal.objects.get()
    cliente = cenario["cliente"]
    for url in (
        reverse("web:documentos"),
        reverse("web:documentos") + "?status=divergente",
        reverse("web:documentos-empresa", args=[cenario["empresa"].pk]),
        reverse("web:documentos-importar", args=[cenario["empresa"].pk]),
        reverse("web:lote", args=[documento.lote_id]),
        reverse("web:documento", args=[documento.pk]),
    ):
        resposta = cliente.get(url)
        assert resposta.status_code == 200, url
    pagina = cliente.get(reverse("web:documento", args=[documento.pk])).content.decode()
    assert "Com divergência" in pagina and "VAL-120" in pagina and "+3,00" in pagina
    xml = cliente.get(reverse("web:documento-xml", args=[documento.pk]))
    assert xml["Content-Type"].startswith("application/xml") and b"<nfeProc" in xml.content
