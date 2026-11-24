"""Alíquotas efetivas do Simples por período (D3) e itens do catálogo nas simulações."""

import pytest

from auditoria.models import Auditoria
from contas.papeis import Papel
from empresas.models import Empresa
from simulacoes.models import AliquotaEfetivaSN, Simulacao, SimulacaoItem

URL_SN = "/api/simulacoes/simples/"
URL_RR = "/api/simulacoes/regime-regular/"
URL_CADASTRO = "/api/aliquotas-simples/"
MOTOR_SN = "/api/calculadora/observabilidade/simples-nacional/nao-assistido"
MOTOR_RR = "/api/calculadora/observabilidade/regime-geral"


@pytest.fixture
def cenario(criar_escritorio, criar_empresa, cliente_de, motores_falsos):
    escritorio, _, dono = criar_escritorio()
    simples = criar_empresa(escritorio, regime=Empresa.Regime.SIMPLES, crt=Empresa.CRT.SIMPLES)
    return {"escritorio": escritorio, "simples": simples, "cliente": cliente_de(dono)}


def corpo_simulacao(empresa, **extra):
    return {
        "empresa": str(empresa.pk),
        "data": "2027-03-15",
        "tp_doc": "55",
        "itens": [{"c_ativ_sn": "01", "c_class_trib": "000001", "base_calculo": "1000.00"}],
        **extra,
    }


def corpo_cadastro(empresa, **extra):
    return {
        "empresa": str(empresa.pk),
        "periodo": "2027-03",
        "fator_r": True,
        "anexos": {"I": {"cbs": "1.1612", "ibs": "0.0129"}},
        **extra,
    }


def test_sem_cadastro_do_periodo_nao_chama_o_motor(cenario, motores_falsos):
    resposta = cenario["cliente"].post(URL_SN, corpo_simulacao(cenario["simples"]), format="json")
    assert resposta.status_code == 400
    assert "03/2027" in resposta.json()["aliquotas_efetivas"][0]
    assert motores_falsos.ultima("sn") is None
    assert not Simulacao.objects.exists()


def test_simulacao_usa_o_cadastro_do_mes(cenario, motores_falsos):
    motores_falsos.gravada("sn", MOTOR_SN, "simples_nfe_crt1.json")
    assert cenario["cliente"].post(URL_CADASTRO, corpo_cadastro(cenario["simples"]), format="json").status_code == 201

    resposta = cenario["cliente"].post(URL_SN, corpo_simulacao(cenario["simples"]), format="json")
    assert resposta.status_code == 201, resposta.content
    enviado = motores_falsos.json_da_ultima("sn")["aliquotasEfetivas"]
    assert enviado["fatorR"] is True
    assert enviado["anexoI"] == {"pCBSSN": "1.1612", "pIBSSN": "0.0129"}
    origem = resposta.json()["itens"][0]["origem_aliquotas"]
    assert origem["origem"] == "cadastro_do_periodo" and origem["periodo"] == "2027-03"
    assert origem["aliquota_efetiva_sn_id"] == str(AliquotaEfetivaSN.objects.get().pk)


def test_aliquotas_na_requisicao_ainda_valem_so_para_a_simulacao(cenario, motores_falsos):
    motores_falsos.gravada("sn", MOTOR_SN, "simples_nfe_crt1.json")
    efetivas = {"fator_r": True, "anexos": {"I": {"cbs": "1.1612", "ibs": "0.0129"}}}
    corpo = corpo_simulacao(cenario["simples"], aliquotas_efetivas=efetivas)
    resposta = cenario["cliente"].post(URL_SN, corpo, format="json")
    assert resposta.status_code == 201
    assert resposta.json()["itens"][0]["origem_aliquotas"]["origem"] == "informada_pelo_usuario"
    assert not AliquotaEfetivaSN.objects.exists()


def test_cadastro_crud_e_auditoria(cenario):
    cliente = cenario["cliente"]
    criado = cliente.post(URL_CADASTRO, corpo_cadastro(cenario["simples"], periodo="03/2027"), format="json")
    assert criado.status_code == 201, criado.content
    assert criado.json()["periodo"] == "2027-03"
    url = f"{URL_CADASTRO}{criado.json()['id']}/"

    repetido = cliente.post(URL_CADASTRO, corpo_cadastro(cenario["simples"]), format="json")
    assert repetido.status_code == 400 and "periodo" in repetido.json()

    editado = cliente.patch(url, {"anexos": {"I": {"cbs": "1.2", "ibs": "0.02"}}}, format="json")
    assert editado.status_code == 200, editado.content
    assert editado.json()["anexos"]["I"] == {"cbs": "1.2000", "ibs": "0.0200"}
    assert editado.json()["fator_r"] is True  # PATCH parcial mantém o resto
    auditoria = Auditoria.objects.get(acao="aliquota_sn.editar")
    assert auditoria.antes["anexos"]["I"]["cbs"] == "1.1612"

    assert cliente.delete(url).status_code == 204
    assert not AliquotaEfetivaSN.objects.exists()


def test_cadastro_rejeita_empresa_regular_e_anexo_invalido(cenario, criar_empresa):
    regular = criar_empresa(cenario["escritorio"])
    assert cenario["cliente"].post(URL_CADASTRO, corpo_cadastro(regular), format="json").status_code == 400
    corpo = corpo_cadastro(cenario["simples"], anexos={"VI": {"cbs": "1", "ibs": "1"}})
    assert cenario["cliente"].post(URL_CADASTRO, corpo, format="json").status_code == 400
    corpo = corpo_cadastro(cenario["simples"], periodo="2027-13")
    assert cenario["cliente"].post(URL_CADASTRO, corpo, format="json").status_code == 400


def test_assistente_nao_informa_aliquotas(cenario, adicionar_membro, cliente_de):
    assistente = cliente_de(adicionar_membro(cenario["escritorio"], Papel.ASSISTENTE))
    assert assistente.post(URL_CADASTRO, corpo_cadastro(cenario["simples"]), format="json").status_code == 403
    assert assistente.get(URL_CADASTRO).status_code == 200


# --- Itens do catálogo na simulação ----------------------------------------------------------------


def test_item_do_catalogo_completa_os_codigos(cenario, criar_empresa, criar_item, motores_falsos):
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_2026.json")
    regular = criar_empresa(cenario["escritorio"])
    item = criar_item(regular)
    corpo = {
        "empresa": str(regular.pk),
        "data_fato_gerador": "2026-06-10T10:00:00-03:00",
        "itens": [{"item_catalogo": str(item.pk), "base_calculo": "1000.00"}],
    }
    resposta = cenario["cliente"].post(URL_RR, corpo, format="json")
    assert resposta.status_code == 201, resposta.content
    enviado = motores_falsos.json_da_ultima("rr")["itens"][0]
    assert (enviado["ncm"], enviado["cst"], enviado["cClassTrib"]) == ("22030000", "000", "000001")
    assert resposta.json()["itens"][0]["item_catalogo"] == str(item.pk)
    assert SimulacaoItem.objects.get().item_catalogo == item


def test_item_do_catalogo_de_outra_empresa(cenario, criar_empresa, criar_item):
    regular = criar_empresa(cenario["escritorio"])
    outra = criar_empresa(cenario["escritorio"])
    corpo = {
        "empresa": str(regular.pk),
        "data_fato_gerador": "2026-06-10T10:00:00-03:00",
        "itens": [{"item_catalogo": str(criar_item(outra).pk), "base_calculo": "1000.00"}],
    }
    resposta = cenario["cliente"].post(URL_RR, corpo, format="json")
    assert resposta.status_code == 400
    assert "outra empresa" in str(resposta.json())


def test_sem_catalogo_continua_exigindo_cst(cenario, criar_empresa):
    regular = criar_empresa(cenario["escritorio"])
    corpo = {
        "empresa": str(regular.pk),
        "data_fato_gerador": "2026-06-10T10:00:00-03:00",
        "itens": [{"ncm": "22030000", "c_class_trib": "000001", "base_calculo": "10"}],
    }
    resposta = cenario["cliente"].post(URL_RR, corpo, format="json")
    assert resposta.status_code == 400 and "cst" in str(resposta.json())
