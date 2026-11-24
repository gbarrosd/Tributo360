"""Clientes dos motores contra respostas reais gravadas (sem rede).

As respostas em `respostas/` foram gravadas dos motores rodando nas tags fixadas nos submódulos
(regime regular 1.7.2, Simples 1.0.2, base V0073). Os testes de contrato (`test_contrato.py`) repetem
os mesmos casos contra os motores de verdade.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from conftest import resposta_gravada
from engines import clientes
from engines.clientes.danfse import DanfseRecusado
from engines.clientes.regime_regular import payload_operacao
from engines.contratos import (
    AliquotaAnexoSN,
    AliquotasEfetivasSN,
    AliquotasNominais,
    EstadoItem,
    ImpostoSeletivo,
    ItemRegular,
    ItemSimples,
    Motor,
    OperacaoRegular,
    OperacaoSimples,
    ParAliquotaSN,
)
from engines.http import MotorIndisponivel, OperacaoRecusada

D = Decimal
CAMINHO_RR = "/api/calculadora/observabilidade/regime-geral"
CAMINHO_SN = "/api/calculadora/observabilidade/simples-nacional/nao-assistido"


def operacao_oficial_2027():
    """O exemplo de 4 itens do `docs/exemplos.md` do regime regular, no nosso contrato."""
    aliq = AliquotasNominais(cbs=D("8.8"), ibs_estadual=D("0.05"), ibs_municipal=D("0.05"))
    return OperacaoRegular(
        id="6194602ea71cbf9431c236de4409d920",
        fato_gerador=datetime(2027, 3, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
        uf="RS",
        municipio=4314902,
        itens=(
            ItemRegular(
                1, "200", "200003", D("100.00"), ncm="10063021", quantidade=D("20"), unidade="KG", aliquotas=aliq
            ),
            ItemRegular(
                2,
                "000",
                "000001",
                ncm="22030000",
                quantidade=D("12"),
                unidade="LT",
                aliquotas=AliquotasNominais(D("8.8"), D("0.05"), D("0.05"), is_ad_valorem=D("10.0")),
                imposto_seletivo=ImpostoSeletivo("000", "000001", D("150.00"), D("12"), "LT", D("15.00")),
            ),
            ItemRegular(3, "200", "200028", D("1200.00"), nbs="122011200", aliquotas=aliq),
            ItemRegular(4, "200", "200035", D("80.00"), ncm="02013000", aliquotas=aliq),
        ),
    )


def operacao_simples_nfe():
    return OperacaoSimples(
        id="op-2",
        data=date(2027, 3, 15),
        tp_doc="55",
        crt="1",
        aliquotas_efetivas=AliquotasEfetivasSN(
            fator_r=True,
            impedimento_sublimite=False,
            anexos={"I": AliquotaAnexoSN(ParAliquotaSN(D("1.1612"), D("0.0129")))},
        ),
        itens=(ItemSimples(1, "01", "000001", D("1000.00"), valor_imposto_seletivo=D("0.00")),),
    )


# --- Regime regular -----------------------------------------------------------------------------


def test_payload_equivale_ao_exemplo_oficial():
    """O que mandamos é o exemplo oficial (com números como texto, que o motor aceita)."""
    gerado = payload_operacao(operacao_oficial_2027())
    oficial = resposta_gravada("regime_regular_exemplo_oficial_2027.json")["entrada"]

    def normalizar(valor):
        if isinstance(valor, dict):
            return {k: normalizar(v) for k, v in valor.items()}
        if isinstance(valor, list):
            return [normalizar(v) for v in valor]
        if isinstance(valor, int | float | str) and not isinstance(valor, bool):
            try:
                return D(str(valor)).normalize()
            except ArithmeticError:
                return valor
        return valor

    assert normalizar(gerado) == normalizar(oficial)


def test_interpreta_exemplo_oficial_2027(motores_falsos):
    motores_falsos.gravada("rr", CAMINHO_RR, "regime_regular_exemplo_oficial_2027.json")
    resultado = clientes.regime_regular().calcular(operacao_oficial_2027(), contexto={"simulacao_id": "x"})

    assert resultado.motor == Motor.REGIME_REGULAR
    assert (resultado.versao.versao_app, resultado.versao.versao_db) == ("1.7.2", "V0073")
    assert resultado.simulado is True and resultado.aviso_simulado == "6"
    assert [i.estado for i in resultado.itens] == [EstadoItem.CALCULO_SIMULADO] * 3 + [
        EstadoItem.INCONSISTENCIA_ENTRADA
    ]
    assert resultado.totais.cbs == D("56.76")
    assert resultado.totais.ibs == D("0.64")
    assert resultado.totais.imposto_seletivo == D("15.00")
    assert resultado.totais.base_calculo == D("1465.00")
    assert not resultado.todos_calcularam

    educacao = resultado.itens[2].valores
    assert (educacao.base_calculo, educacao.cbs, educacao.ibs) == (D("1200.00"), D("42.24"), D("0.48"))
    assert resultado.itens[1].valores.imposto_seletivo == D("15.00")
    assert "Art. 129" in resultado.itens[2].memoria["cbs"]

    erro = resultado.itens[3].erros[0]
    assert (erro.codigo, erro.campo) == ("REG-011", "#/itens/3/ncm")


def test_interpreta_2026_sem_aliquotas(motores_falsos):
    motores_falsos.gravada("rr", CAMINHO_RR, "regime_regular_2026.json")
    op = OperacaoRegular(
        fato_gerador=datetime(2026, 6, 10, 10, tzinfo=ZoneInfo("America/Sao_Paulo")),
        uf="SP",
        municipio=3550308,
        itens=(ItemRegular(1, "000", "000001", D("1000.00"), ncm="22030000", quantidade=D("1"), unidade="UN"),),
    )
    resultado = clientes.regime_regular().calcular(op)
    assert resultado.simulado is False
    item = resultado.itens[0]
    assert item.estado == EstadoItem.CALCULADO
    assert (item.valores.aliquota_cbs, item.valores.aliquota_ibs_uf) == (D("0.90"), D("0.10"))
    assert (resultado.totais.cbs, resultado.totais.ibs) == (D("9.00"), D("1.00"))
    assert "aliquotasNominais" not in motores_falsos.json_da_ultima("rr")["itens"][0]


def test_operacao_recusada_inteira_vira_excecao_com_erros(motores_falsos):
    motores_falsos.rotas[("rr", "POST", CAMINHO_RR)] = httpx.Response(
        422,
        json={
            "detail": "O cálculo contém 1 erro(s).",
            "errors": [{"code": "VAL-003", "title": "Lista vazia", "pointer": "#/itens", "detail": "vazio"}],
        },
    )
    with pytest.raises(OperacaoRecusada) as exc:
        clientes.regime_regular().calcular(operacao_oficial_2027())
    assert exc.value.status == 422
    assert exc.value.erros[0].codigo == "VAL-003"
    assert len(motores_falsos.recebidas) == 1  # 4xx não é repetido


def test_validar_xml(motores_falsos):
    def validar(request):
        if b"<IBSCBS>" in request.content:
            return httpx.Response(200, text="true")
        detalhe = "Erro na validação do XML: Erro na linha 1, coluna 4: cvc-elt.1.a: Cannot find the declaration"
        return httpx.Response(422, json={"detail": detalhe})

    motores_falsos.rotas[("rr", "POST", "/api/calculadora/xml/validate")] = validar
    cliente = clientes.regime_regular()
    assert cliente.validar_xml(b"<IBSCBS></IBSCBS>", "nfe", "grupo").valido is True
    invalido = cliente.validar_xml(b"<a/>", "nfe", "grupo")
    assert (invalido.valido, invalido.linha, invalido.coluna) == (False, 1, 4)
    assert motores_falsos.ultima().url.params["subtipo"] == "grupo"
    with pytest.raises(ValueError):
        cliente.validar_xml(b"<a/>", "danfe")


def test_versao_do_regime_regular(motores_falsos):
    versao = clientes.regime_regular().versao()
    assert (versao.versao_app, versao.versao_db, versao.data_db) == ("1.7.2", "V0073", date(2026, 11, 9))


# --- Simples ------------------------------------------------------------------------------------


def test_payload_simples_equivale_ao_exemplo_do_swagger():
    from engines.clientes.simples import payload_operacao as payload_sn

    oficial = resposta_gravada("simples_nfe_crt1.json")["entrada"]
    assert payload_sn(operacao_simples_nfe()) == oficial


def test_interpreta_simples_nfe(motores_falsos):
    motores_falsos.gravada("sn", CAMINHO_SN, "simples_nfe_crt1.json")
    resultado = clientes.simples().calcular(operacao_simples_nfe())
    assert resultado.motor == Motor.SIMPLES
    assert (resultado.versao.versao_app, resultado.versao.versao_db) == ("1.0.2", "V0073")
    item = resultado.itens[0]
    assert item.estado == EstadoItem.CALCULADO
    assert (item.valores.cbs, item.valores.ibs) == (D("11.61"), D("0.13"))
    assert item.extras["resolucao_aliquota"]["anexoAplicado"] == "I"
    assert (resultado.totais.cbs, resultado.totais.ibs, resultado.totais.tributos) == (
        D("11.61"),
        D("0.13"),
        D("11.74"),
    )
    assert resultado.extras["regime_apuracao"] == "SN_PLENO"


def test_interpreta_erro_por_item_no_simples(motores_falsos):
    motores_falsos.gravada("sn", CAMINHO_SN, "simples_nfse_exemplo_swagger.json")
    resultado = clientes.simples().calcular(operacao_simples_nfe())
    assert resultado.itens[0].estado == EstadoItem.INCONSISTENCIA_ENTRADA
    assert resultado.itens[0].erros[0].codigo == "VSN-050"
    assert resultado.totais.cbs == D("0")  # sem bloco "total" na resposta


# --- DANFSe -------------------------------------------------------------------------------------


def test_danfse_devolve_pdf(motores_falsos):
    motores_falsos.rotas[("df", "POST", "/api/danfse")] = httpx.Response(
        200, content=b"%PDF-1.7 ...", headers={"Content-Type": "application/pdf"}
    )
    assert clientes.danfse().gerar(b"<NFSe/>", canhoto=True).startswith(b"%PDF")
    assert motores_falsos.ultima().url.params["canhoto"] == "true"


def test_danfse_400_vira_mensagem(motores_falsos):
    motores_falsos.rotas[("df", "POST", "/api/danfse")] = httpx.Response(
        400, json={"sucesso": False, "mensagem": "EOF"}
    )
    with pytest.raises(DanfseRecusado, match="EOF"):
        clientes.danfse().gerar(b"lixo")


def test_danfse_versao_fixa(motores_falsos):
    assert clientes.danfse().versao().versao_app == "1.0.0"


# --- Política de rede: nova tentativa e circuit breaker -------------------------------------------


def test_repete_uma_vez_em_5xx(motores_falsos):
    dados = resposta_gravada("regime_regular_2026.json")
    respostas = iter([httpx.Response(503), httpx.Response(200, json=dados["resposta"], headers=dados["headers"])])
    motores_falsos.rotas[("rr", "POST", CAMINHO_RR)] = lambda request: next(respostas)
    resultado = clientes.regime_regular().calcular(operacao_oficial_2027())
    assert resultado.itens[0].estado == EstadoItem.CALCULADO
    assert len(motores_falsos.recebidas) == 2


def test_motor_fora_do_ar_vira_indisponivel_depois_de_duas_tentativas(motores_falsos):
    motores_falsos.fora_do_ar.add("rr")
    with pytest.raises(MotorIndisponivel):
        clientes.regime_regular().calcular(operacao_oficial_2027())
    assert len(motores_falsos.recebidas) == 2


def test_circuito_abre_e_nao_chama_mais_o_motor(motores_falsos, settings):
    settings.MOTORES_CIRCUITO_FALHAS = 3
    motores_falsos.fora_do_ar.add("sn")
    for _ in range(3):
        with pytest.raises(MotorIndisponivel):
            clientes.simples().versao()
    chamadas = len(motores_falsos.recebidas)
    with pytest.raises(MotorIndisponivel, match="temporariamente"):
        clientes.simples().versao()
    assert len(motores_falsos.recebidas) == chamadas  # circuito aberto: nem tentou

    # Um motor não derruba o outro.
    assert clientes.regime_regular().versao().versao_app == "1.7.2"


def test_sucesso_zera_contagem_de_falhas(motores_falsos, settings):
    settings.MOTORES_CIRCUITO_FALHAS = 2
    motores_falsos.fora_do_ar.add("sn")
    with pytest.raises(MotorIndisponivel):
        clientes.simples().versao()
    motores_falsos.fora_do_ar.clear()
    clientes.simples().versao()
    motores_falsos.fora_do_ar.add("sn")
    with pytest.raises(MotorIndisponivel, match=r"\(ConnectError\)"):
        clientes.simples().versao()  # 1ª falha depois do sucesso: circuito continua fechado


def test_timeouts_da_politica(settings):
    cliente = clientes.regime_regular()
    assert cliente._timeout.read == settings.MOTORES_TIMEOUT_CALCULO == 5
    assert cliente._timeout.connect == settings.MOTORES_TIMEOUT_CONEXAO == 2
