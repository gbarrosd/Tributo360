"""Testes de contrato: os clientes contra os motores de verdade, com casos dourados.

Rodam só com ``MOTORES_CONTRATO=1`` (no CI, job "contrato", que sobe os motores pelo compose):

    docker compose -f docker-compose.yml -f docker-compose.contrato.yml up -d --build --wait \\
        motor-regime-regular motor-simples motor-danfse
    MOTORES_CONTRATO=1 MOTOR_REGIME_REGULAR_URL=http://localhost:18080 \\
        MOTOR_SIMPLES_URL=http://localhost:18082 MOTOR_DANFSE_URL=http://localhost:18090 pytest -m contrato

Os valores esperados são os que os motores devolvem nas tags fixadas nos submódulos. Quando uma tag
muda e um caso quebra, a diferença é exatamente o que precisa de revisão antes de ir para produção
(planejamento, seção 6). Casos marcados "problema conhecido" documentam um defeito relatado na Receita:
quando o motor corrigir, o teste falha de propósito para atualizarmos o caso.
"""

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engines import clientes
from engines.clientes.danfse import DanfseRecusado
from engines.contratos import (
    AliquotaAnexoSN,
    AliquotasEfetivasSN,
    EstadoItem,
    ImpostoSeletivo,
    ItemRegular,
    ItemSimples,
    OperacaoRegular,
    OperacaoSimples,
    ParAliquotaSN,
)

from .test_clientes import operacao_oficial_2027, operacao_simples_nfe

pytestmark = [
    pytest.mark.contrato,
    pytest.mark.skipif(os.environ.get("MOTORES_CONTRATO") != "1", reason="defina MOTORES_CONTRATO=1"),
]

D = Decimal
SP = ZoneInfo("America/Sao_Paulo")
NFSE_TESTE = Path(__file__).parent / "respostas" / "nfse_autorizada_teste.xml"

# Versões fixadas nos submódulos. Trocar a tag = atualizar aqui depois de revisar os casos.
VERSAO_RR = ("1.7.2", "V0073")
VERSAO_SN = ("1.0.2", "V0073")


@pytest.fixture(autouse=True)
def _urls_do_ambiente(settings):
    for motor, variavel in (
        ("regime_regular", "MOTOR_REGIME_REGULAR_URL"),
        ("simples", "MOTOR_SIMPLES_URL"),
        ("danfse", "MOTOR_DANFSE_URL"),
    ):
        if os.environ.get(variavel):
            settings.MOTORES = {**settings.MOTORES, motor: {**settings.MOTORES[motor], "url": os.environ[variavel]}}


def operacao_2026(*itens):
    return OperacaoRegular(fato_gerador=datetime(2026, 6, 10, 10, tzinfo=SP), uf="SP", municipio=3550308, itens=itens)


# --- Regime regular -----------------------------------------------------------------------------


def test_versao_regime_regular():
    versao = clientes.regime_regular().versao()
    assert (versao.versao_app, versao.versao_db) == VERSAO_RR
    assert versao.data_db == date(2026, 11, 9)


def test_dourado_exemplo_oficial_2027():
    """docs/exemplos.md do motor: 3 itens simulados, 1 com REG-011, totais 56,76 / 0,64 / 15,00."""
    resultado = clientes.regime_regular().calcular(operacao_oficial_2027())
    assert (resultado.versao.versao_app, resultado.versao.versao_db) == VERSAO_RR
    assert resultado.simulado and resultado.aviso_simulado
    assert [i.estado for i in resultado.itens] == [EstadoItem.CALCULO_SIMULADO] * 3 + [
        EstadoItem.INCONSISTENCIA_ENTRADA
    ]
    assert (resultado.totais.base_calculo, resultado.totais.cbs, resultado.totais.ibs) == (
        D("1465.00"),
        D("56.76"),
        D("0.64"),
    )
    assert resultado.totais.imposto_seletivo == D("15.00")

    arroz, cerveja, educacao, erro = resultado.itens
    assert (arroz.valores.cbs, arroz.valores.ibs) == (D("0.00"), D("0.00"))  # cesta básica, redução de 100%
    # Base de CBS/IBS da cerveja = base do IS + IS (150 + 15).
    assert (cerveja.valores.base_calculo, cerveja.valores.cbs, cerveja.valores.ibs) == (
        D("165.00"),
        D("14.52"),
        D("0.16"),
    )
    # Serviço de educação (NBS) com redução de 60%: cobre a rota NBS citada na issue #19 do motor.
    assert (educacao.valores.cbs, educacao.valores.ibs) == (D("42.24"), D("0.48"))
    assert [(e.codigo, e.campo) for e in erro.erros] == [("REG-011", "#/itens/3/ncm")]


def test_dourado_2026_com_aliquotas_de_teste_do_motor():
    resultado = clientes.regime_regular().calcular(
        operacao_2026(ItemRegular(1, "000", "000001", D("1000.00"), ncm="22030000", quantidade=D("1"), unidade="UN"))
    )
    item = resultado.itens[0]
    assert item.estado == EstadoItem.CALCULADO and not resultado.simulado
    assert (item.valores.aliquota_cbs, item.valores.aliquota_ibs_uf, item.valores.aliquota_ibs_mun) == (
        D("0.90"),
        D("0.10"),
        D("0.00"),
    )
    assert (resultado.totais.cbs, resultado.totais.ibs) == (D("9.00"), D("1.00"))


def test_dourado_2027_sem_aliquotas_e_erro_por_item():
    """Confirma a premissa do nosso bloqueio antes do motor: sem alíquota em 2027, REG-037 no item."""
    op = OperacaoRegular(
        fato_gerador=datetime(2027, 6, 10, 10, tzinfo=SP),
        uf="SP",
        municipio=3550308,
        itens=(ItemRegular(1, "000", "000001", D("1000.00"), ncm="22030000"),),
    )
    resultado = clientes.regime_regular().calcular(op)
    assert resultado.itens[0].estado == EstadoItem.INCONSISTENCIA_ENTRADA
    assert resultado.itens[0].erros[0].codigo == "REG-037"


def test_dourado_imposto_seletivo_em_2026():
    """IS em 2026 (issue #20 do motor fala de IS somado em 2026). Hoje o CST do IS não existe em 2026: REG-004.

    Se este caso mudar, o motor passou a tratar IS em 2026 e o resultado precisa de revisão tributária.
    """
    seletivo = ImpostoSeletivo("000", "000001", D("150.00"), D("12"), "LT", D("15.00"))
    item = ItemRegular(1, "000", "000001", ncm="22030000", quantidade=D("12"), unidade="LT", imposto_seletivo=seletivo)
    resultado = clientes.regime_regular().calcular(operacao_2026(item))
    assert resultado.itens[0].estado == EstadoItem.INCONSISTENCIA_ENTRADA
    assert [(e.codigo, e.campo) for e in resultado.itens[0].erros] == [("REG-004", "#/itens/0/cst")]
    assert resultado.totais.imposto_seletivo == D("0")


def test_operacao_sem_itens_e_recusada_inteira():
    from engines.http import OperacaoRecusada

    with pytest.raises(OperacaoRecusada) as exc:
        clientes.regime_regular().calcular(operacao_2026())
    assert exc.value.status == 422
    assert "VAL-003" in {e.codigo for e in exc.value.erros}


def test_validacao_de_xml():
    cliente = clientes.regime_regular()
    invalido = cliente.validar_xml(b"<a><b/></a>", "nfe", "grupo")
    assert invalido.valido is False
    assert (invalido.linha, invalido.coluna) == (1, 4)


def test_dados_abertos_para_sincronizacao():
    cliente = clientes.regime_regular()
    hoje = date(2027, 1, 1)
    cst = {c["codigo"] for c in cliente.situacoes_tributarias(hoje)}
    assert {"000", "200"} <= cst
    assert any(c["codigo"] == "200028" for c in cliente.classificacoes_tributarias(hoje))
    assert any(n["ncm"] == "22030000" for n in cliente.ncm_vigentes(hoje))
    assert any(n["codigo"] == "122011200" for n in cliente.nbs(hoje))
    assert cliente.aliquota_referencia("uniao", date(2026, 6, 1)) == D("0.9")
    assert cliente.aliquota_referencia("uniao", date(2027, 6, 1)) is None  # só há alíquota de 2026


# --- Simples ------------------------------------------------------------------------------------


def test_versao_simples():
    versao = clientes.simples().versao()
    assert (versao.versao_app, versao.versao_db) == VERSAO_SN


def test_dourado_simples_nfe_crt1():
    """Exemplo "NF-e — CRT 1" do Swagger: anexo I, CBS 11,61 e IBS 0,13 sobre R$ 1.000."""
    resultado = clientes.simples().calcular(operacao_simples_nfe())
    assert (resultado.versao.versao_app, resultado.versao.versao_db) == VERSAO_SN
    item = resultado.itens[0]
    assert item.estado == EstadoItem.CALCULADO
    assert (item.valores.cbs, item.valores.ibs) == (D("11.61"), D("0.13"))
    assert item.extras["resolucao_aliquota"]["anexoAplicado"] == "I"
    assert resultado.totais.tributos == D("11.74")


def test_dourado_simples_nfse_exemplo_do_swagger_problema_conhecido():
    """Problema conhecido: o exemplo NFS-e do próprio Swagger falha com VSN-050 na base V0073."""
    op = OperacaoSimples(
        data=date(2027, 3, 15),
        tp_doc="91",
        op_simp_nac="3",
        reg_ap_ibscbs_sn="1",
        reg_ap_trib_sn="1",
        aliquotas_efetivas=AliquotasEfetivasSN(
            fator_r=False,
            impedimento_sublimite=False,
            anexos={
                "III": AliquotaAnexoSN(
                    ParAliquotaSN(D("2.3874"), D("0.0263")),
                    sem_redistribuicao_iss=ParAliquotaSN(D("2.3591"), D("0.0260")),
                )
            },
        ),
        itens=(
            ItemSimples(
                1,
                "12",
                "000001",
                D("10000.00"),
                nbs="114051000",
                nfse={"tribISSQN": "1", "tpRetISSQN": "1", "tpBM": "1"},
            ),
        ),
    )
    resultado = clientes.simples().calcular(op)
    assert resultado.itens[0].erros[0].codigo == "VSN-050"
    # A escolha do par sem redistribuição do ISS acontece mesmo assim.
    assert resultado.itens[0].extras["resolucao_aliquota"]["tipoAliquota"] == "SEM_REDISTRIBUICAO"


def test_atividades_do_simples():
    atividades = clientes.simples().atividades(date(2027, 1, 1))
    assert len(atividades) == 15
    assert atividades[0]["codigo"] == "01"


# --- DANFSe -------------------------------------------------------------------------------------


def test_danfse_gera_pdf():
    pdf = clientes.danfse().gerar(NFSE_TESTE.read_bytes())
    assert pdf.startswith(b"%PDF")


def test_danfse_xml_invalido():
    with pytest.raises(DanfseRecusado):
        clientes.danfse().gerar(b"lixo")


# --- Ponta a ponta pelo Django ------------------------------------------------------------------


@pytest.mark.django_db
def test_simulacao_pela_api_grava_versao_real(criar_escritorio, criar_empresa, cliente_de):
    escritorio, _, dono = criar_escritorio()
    empresa = criar_empresa(escritorio)
    resposta = cliente_de(dono).post(
        "/api/simulacoes/regime-regular/",
        {
            "empresa": str(empresa.pk),
            "data_fato_gerador": "2026-06-10T10:00:00-03:00",
            "itens": [{"ncm": "22030000", "cst": "000", "c_class_trib": "000001", "base_calculo": "1000.00"}],
        },
        format="json",
    )
    assert resposta.status_code == 201, resposta.content
    corpo = resposta.json()
    assert (corpo["versao_motor"]["versao_app"], corpo["versao_motor"]["versao_db"]) == VERSAO_RR
    assert corpo["versao_motor"]["data_db"] == "2026-11-09"
    assert (corpo["total_cbs"], corpo["total_ibs"]) == ("9.00", "1.00")
