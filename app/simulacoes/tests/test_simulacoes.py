"""Primeiros cálculos pelo Django, com os motores substituídos por respostas reais gravadas."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from auditoria.models import Auditoria
from contas.papeis import Papel
from empresas.models import Empresa
from engines.models import AliquotaNominal, VersaoMotor
from simulacoes.models import Simulacao

URL_RR = "/api/simulacoes/regime-regular/"
URL_SN = "/api/simulacoes/simples/"
MOTOR_RR = "/api/calculadora/observabilidade/regime-geral"
MOTOR_SN = "/api/calculadora/observabilidade/simples-nacional/nao-assistido"


@pytest.fixture
def cenario(criar_escritorio, criar_empresa, cliente_de, motores_falsos):
    escritorio, _, dono = criar_escritorio()
    empresa = criar_empresa(escritorio, uf="RS", municipio_ibge="4314902")
    return {"escritorio": escritorio, "dono": dono, "empresa": empresa, "cliente": cliente_de(dono)}


def corpo_2026(empresa, **extra):
    return {
        "empresa": str(empresa.pk),
        "data_fato_gerador": "2026-06-10T10:00:00-03:00",
        "uf": "SP",
        "municipio": "3550308",
        "itens": [{"ncm": "22030000", "cst": "000", "c_class_trib": "000001", "base_calculo": "1000.00"}],
        **extra,
    }


def corpo_2027(empresa, itens=None, **extra):
    return {
        "empresa": str(empresa.pk),
        "data_fato_gerador": "2027-03-10T10:00:00-03:00",
        "rotulo": "Cenário base",
        "itens": itens
        or [
            {"ncm": "10063021", "cst": "200", "c_class_trib": "200003", "base_calculo": "100.00"},
            {"nbs": "122011200", "cst": "200", "c_class_trib": "200028", "base_calculo": "1200.00"},
        ],
        **extra,
    }


def cadastrar_tabela_2027():
    for esfera, codigo, valor in (("uniao", "", "8.8"), ("uf", "43", "0.05"), ("municipio", "4314902", "0.05")):
        AliquotaNominal.objects.create(
            esfera=esfera,
            codigo_ibge=codigo,
            aliquota=Decimal(valor),
            vigencia_inicio=date(2027, 1, 1),
            fonte="Fonte de teste",
            data_publicacao=date(2026, 12, 1),
        )


# --- Regime regular -----------------------------------------------------------------------------


def test_calculo_2026_grava_simulacao_com_versao(cenario, motores_falsos):
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_2026.json")
    resposta = cenario["cliente"].post(URL_RR, corpo_2026(cenario["empresa"]), format="json")
    assert resposta.status_code == 201, resposta.content
    corpo = resposta.json()

    assert corpo["situacao"] == "calculado" and corpo["simulado"] is False
    assert (corpo["total_cbs"], corpo["total_ibs"]) == ("9.00", "1.00")
    assert corpo["versao_motor"]["versao_app"] == "1.7.2"
    assert corpo["versao_motor"]["data_db"] == "2026-11-09"
    item = corpo["itens"][0]
    assert item["estado"] == "CALCULADO"
    assert item["origem_aliquotas"]["cbs"] == {"origem": "motor_2026", "valor": "0.90"}
    assert "Tributação integral" in item["memoria"]["cbs"]

    enviado = motores_falsos.json_da_ultima("rr")
    assert enviado["id"] == corpo["id"].replace("-", "")
    assert enviado["dhFatoGerador"] == "2026-06-10T10:00:00-03:00"
    assert "aliquotasNominais" not in enviado["itens"][0]

    simulacao = Simulacao.objects.get(pk=corpo["id"])
    assert simulacao.versao_motor == VersaoMotor.objects.get(motor="regime_regular")
    assert simulacao.payload_enviado == enviado
    assert simulacao.resposta_bruta["total"]["tribCalc"]["IBSCBSTot"]["gCBS"]["vCBS"] == "9.00"
    assert Auditoria.objects.filter(acao="simulacao.criar", objeto_id=str(simulacao.pk)).exists()


def test_2026_nao_aceita_aliquota_informada(cenario, motores_falsos):
    corpo = corpo_2026(cenario["empresa"], aliquotas={"cbs": "8.8"})
    resposta = cenario["cliente"].post(URL_RR, corpo, format="json")
    assert resposta.status_code == 400
    assert "2027" in str(resposta.json())
    assert motores_falsos.ultima("rr") is None


def test_2027_usa_a_tabela_e_registra_a_origem(cenario, motores_falsos):
    cadastrar_tabela_2027()
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_exemplo_oficial_2027.json")
    resposta = cenario["cliente"].post(URL_RR, corpo_2027(cenario["empresa"]), format="json")
    assert resposta.status_code == 201, resposta.content
    corpo = resposta.json()

    enviado = motores_falsos.json_da_ultima("rr")
    assert enviado["uf"] == "RS" and enviado["municipio"] == 4314902  # padrão: local da empresa
    assert enviado["itens"][0]["aliquotasNominais"] == {
        "cbs": "8.8000",
        "ibsEstadual": "0.0500",
        "ibsMunicipal": "0.0500",
    }
    origem = corpo["itens"][0]["origem_aliquotas"]
    assert {campo: o["origem"] for campo, o in origem.items()} == {
        "cbs": "tabela_tributo360",
        "ibs_estadual": "tabela_tributo360",
        "ibs_municipal": "tabela_tributo360",
    }
    assert origem["cbs"]["fonte"] == "Fonte de teste"
    # A resposta gravada é o exemplo oficial de 4 itens (o item 4 com REG-011).
    assert corpo["situacao"] == "com_inconsistencia"
    assert corpo["simulado"] is True
    assert corpo["rotulo"] == "Cenário base"


def test_2027_edicao_do_contador_guarda_valor_anterior(cenario, motores_falsos):
    cadastrar_tabela_2027()
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_exemplo_oficial_2027.json")
    itens = [
        {
            "ncm": "10063021",
            "cst": "200",
            "c_class_trib": "200003",
            "base_calculo": "100.00",
            "aliquotas": {"ibs_estadual": "0.07"},
        }
    ]
    corpo = corpo_2027(cenario["empresa"], itens=itens, aliquotas={"cbs": "9.0"})
    resposta = cenario["cliente"].post(URL_RR, corpo, format="json")
    assert resposta.status_code == 201, resposta.content

    enviado = motores_falsos.json_da_ultima("rr")["itens"][0]["aliquotasNominais"]
    assert enviado == {"cbs": "9.0000", "ibsEstadual": "0.0700", "ibsMunicipal": "0.0500"}
    origem = resposta.json()["itens"][0]["origem_aliquotas"]
    assert origem["cbs"]["origem"] == "editada_pelo_usuario" and origem["cbs"]["valor_anterior"] == "8.8000"
    assert origem["ibs_estadual"]["valor_anterior"] == "0.0500"
    assert origem["ibs_municipal"]["origem"] == "tabela_tributo360"


def test_2027_sem_tabela_e_sem_aliquota_orienta_antes_de_chamar_o_motor(cenario, motores_falsos):
    resposta = cenario["cliente"].post(URL_RR, corpo_2027(cenario["empresa"]), format="json")
    assert resposta.status_code == 400
    mensagem = resposta.json()["itens[0].aliquotas"][0]
    assert "CBS, IBS estadual, IBS municipal" in mensagem
    assert motores_falsos.ultima("rr") is None
    assert not Simulacao.objects.exists()


def test_2027_sem_tabela_com_aliquotas_informadas(cenario, motores_falsos):
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_exemplo_oficial_2027.json")
    aliq = {"cbs": "8.8", "ibs_estadual": "0.05", "ibs_municipal": "0.05"}
    resposta = cenario["cliente"].post(URL_RR, corpo_2027(cenario["empresa"], aliquotas=aliq), format="json")
    assert resposta.status_code == 201
    origem = resposta.json()["itens"][0]["origem_aliquotas"]["cbs"]
    assert origem == {
        "origem": "editada_pelo_usuario",
        "valor": "8.8000",
        "valor_anterior": None,
        "aliquota_nominal_id": None,
    }


def test_imposto_seletivo_vai_para_o_motor(cenario, motores_falsos):
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_exemplo_oficial_2027.json")
    itens = [
        {
            "ncm": "22030000",
            "cst": "000",
            "c_class_trib": "000001",
            "quantidade": "12",
            "unidade": "LT",
            "aliquotas": {"cbs": "8.8", "ibs_estadual": "0.05", "ibs_municipal": "0.05", "is_ad_valorem": "10"},
            "imposto_seletivo": {
                "cst": "000",
                "c_class_trib": "000001",
                "base_calculo": "150.00",
                "quantidade": "12",
                "unidade": "LT",
                "imposto_informado": "15.00",
            },
        }
    ]
    resposta = cenario["cliente"].post(URL_RR, corpo_2027(cenario["empresa"], itens=itens), format="json")
    assert resposta.status_code == 201, resposta.content
    enviado = motores_falsos.json_da_ultima("rr")["itens"][0]
    assert enviado["aliquotasNominais"]["impostoSeletivo"] == {"adValorem": "10.0000"}
    assert enviado["impostoSeletivo"]["impostoInformado"] == "15.00"
    assert "baseCalculo" not in enviado


def test_motor_fora_do_ar_responde_503_e_nao_grava(cenario, motores_falsos):
    motores_falsos.fora_do_ar.add("rr")
    resposta = cenario["cliente"].post(URL_RR, corpo_2026(cenario["empresa"]), format="json")
    assert resposta.status_code == 503
    assert resposta.json()["motor"] == "regime_regular"
    assert not Simulacao.objects.exists()


def test_operacao_recusada_responde_422_com_erros_do_motor(cenario, motores_falsos):
    motores_falsos.rotas[("rr", "POST", MOTOR_RR)] = httpx.Response(
        422, json={"detail": "O cálculo contém 1 erro(s).", "errors": [{"code": "VAL-001", "pointer": "#/uf"}]}
    )
    resposta = cenario["cliente"].post(URL_RR, corpo_2026(cenario["empresa"]), format="json")
    assert resposta.status_code == 422
    assert resposta.json()["erros"][0]["codigo"] == "VAL-001"
    assert not Simulacao.objects.exists()


@pytest.mark.parametrize(
    "item",
    [
        {"cst": "000", "c_class_trib": "000001", "base_calculo": "1"},  # sem NCM/NBS
        {"ncm": "2203", "cst": "000", "c_class_trib": "000001", "base_calculo": "1"},  # NCM curto
        {"ncm": "22030000", "cst": "000", "c_class_trib": "000001"},  # sem base nem IS
    ],
)
def test_validacao_de_item(cenario, motores_falsos, item):
    corpo = corpo_2026(cenario["empresa"], itens=[item])
    assert cenario["cliente"].post(URL_RR, corpo, format="json").status_code == 400
    assert motores_falsos.ultima("rr") is None


def test_empresa_do_simples_nao_usa_regime_regular(cenario, criar_empresa):
    simples = criar_empresa(cenario["escritorio"], regime=Empresa.Regime.SIMPLES, crt=Empresa.CRT.SIMPLES)
    resposta = cenario["cliente"].post(URL_RR, corpo_2026(simples), format="json")
    assert resposta.status_code == 400
    assert "Simples" in str(resposta.json()["empresa"])


def test_empresa_de_outro_escritorio(cenario, criar_escritorio, criar_empresa, motores_falsos):
    outro, _, _ = criar_escritorio("Outro")
    alheia = criar_empresa(outro)
    resposta = cenario["cliente"].post(URL_RR, corpo_2026(alheia), format="json")
    assert resposta.status_code == 400
    assert resposta.json()["empresa"] == ["Empresa não encontrada ou inativa."]
    assert motores_falsos.ultima("rr") is None


def test_papel_leitura_nao_simula_mas_ve_historico(cenario, adicionar_membro, cliente_de, criar_simulacao):
    leitura = cliente_de(adicionar_membro(cenario["escritorio"], Papel.LEITURA))
    assert leitura.post(URL_RR, corpo_2026(cenario["empresa"]), format="json").status_code == 403
    criar_simulacao(cenario["empresa"])
    assert leitura.get("/api/simulacoes/").json()["count"] == 1


def test_assistente_simula(cenario, adicionar_membro, cliente_de, motores_falsos):
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_2026.json")
    assistente = cliente_de(adicionar_membro(cenario["escritorio"], Papel.ASSISTENTE))
    assert assistente.post(URL_RR, corpo_2026(cenario["empresa"]), format="json").status_code == 201


def test_historico_filtra_por_empresa_e_detalha(cenario, criar_empresa, criar_simulacao):
    outra = criar_empresa(cenario["escritorio"])
    alvo = criar_simulacao(cenario["empresa"])
    criar_simulacao(outra)
    cliente = cenario["cliente"]
    lista = cliente.get("/api/simulacoes/", {"empresa": str(cenario["empresa"].pk)}).json()
    assert [s["id"] for s in lista["results"]] == [str(alvo.pk)]
    assert lista["results"][0]["versao_motor"] == "regime_regular 1.7.2 · base V0073"
    detalhe = cliente.get(f"/api/simulacoes/{alvo.pk}/").json()
    assert detalhe["versao_motor"]["versao_db"] == "V0073"
    assert detalhe["itens"] == []


# --- Simples Nacional ---------------------------------------------------------------------------


@pytest.fixture
def empresa_simples(cenario, criar_empresa):
    return criar_empresa(cenario["escritorio"], regime=Empresa.Regime.SIMPLES, crt=Empresa.CRT.SIMPLES)


def corpo_simples(empresa, **extra):
    return {
        "empresa": str(empresa.pk),
        "data": "2027-03-15",
        "tp_doc": "55",
        "aliquotas_efetivas": {"fator_r": True, "anexos": {"I": {"cbs": "1.1612", "ibs": "0.0129"}}},
        "itens": [
            {"c_ativ_sn": "01", "c_class_trib": "000001", "base_calculo": "1000.00", "valor_imposto_seletivo": "0.00"}
        ],
        **extra,
    }


def test_simples_nfe(cenario, empresa_simples, motores_falsos):
    motores_falsos.gravada("sn", MOTOR_SN, "simples_nfe_crt1.json")
    resposta = cenario["cliente"].post(URL_SN, corpo_simples(empresa_simples), format="json")
    assert resposta.status_code == 201, resposta.content
    corpo = resposta.json()
    assert corpo["tipo"] == "simples" and corpo["situacao"] == "calculado"
    assert (corpo["total_cbs"], corpo["total_ibs"]) == ("11.61", "0.13")
    assert corpo["versao_motor"]["motor"] == "simples"
    origem = corpo["itens"][0]["origem_aliquotas"]
    assert origem == {
        "origem": "informada_pelo_usuario",
        "anexo": "I",
        "tipo": "PAR_UNICO",
        "pCBSSN": "1.1612",
        "pIBSSN": "0.0129",
    }

    enviado = motores_falsos.json_da_ultima("sn")
    assert enviado["CRT"] == "1"  # padrão: CRT da empresa
    assert enviado["aliquotasEfetivas"] == {
        "fatorR": True,
        "impedimentoSublimite": False,
        "anexoI": {"pCBSSN": "1.1612", "pIBSSN": "0.0129"},
    }


def test_simples_nfse_usa_campos_da_nfse(cenario, empresa_simples, motores_falsos):
    motores_falsos.gravada("sn", MOTOR_SN, "simples_nfse_exemplo_swagger.json")
    corpo = corpo_simples(
        empresa_simples,
        tp_doc="91",
        reg_ap_ibscbs_sn="1",
        reg_ap_trib_sn="1",
        aliquotas_efetivas={
            "fator_r": False,
            "anexos": {
                "III": {"cbs": "2.3874", "ibs": "0.0263", "sem_redistribuicao_iss": {"cbs": "2.3591", "ibs": "0.0260"}}
            },
        },
        itens=[
            {
                "c_ativ_sn": "12",
                "c_class_trib": "000001",
                "nbs": "114051000",
                "base_calculo": "10000.00",
                "trib_issqn": "1",
                "tp_ret_issqn": "1",
                "tp_bm": "1",
            }
        ],
    )
    resposta = cenario["cliente"].post(URL_SN, corpo, format="json")
    assert resposta.status_code == 201, resposta.content
    assert resposta.json()["situacao"] == "com_inconsistencia"
    assert resposta.json()["itens"][0]["erros"][0]["codigo"] == "VSN-050"

    enviado = motores_falsos.json_da_ultima("sn")
    assert "CRT" not in enviado and enviado["opSimpNac"] == "3"
    assert enviado["itens"][0]["tpBM"] == "1"
    assert enviado["aliquotasEfetivas"]["anexoIII"]["semRedistribuicaoISS"] == {"pCBSSN": "2.3591", "pIBSSN": "0.0260"}


def test_simples_rejeita_anexo_invalido(cenario, empresa_simples, motores_falsos):
    corpo = corpo_simples(
        empresa_simples, aliquotas_efetivas={"fator_r": False, "anexos": {"VI": {"cbs": 1, "ibs": 1}}}
    )
    assert cenario["cliente"].post(URL_SN, corpo, format="json").status_code == 400
    assert motores_falsos.ultima("sn") is None


def test_empresa_regular_nao_usa_simples(cenario, motores_falsos):
    resposta = cenario["cliente"].post(URL_SN, corpo_simples(cenario["empresa"]), format="json")
    assert resposta.status_code == 400
