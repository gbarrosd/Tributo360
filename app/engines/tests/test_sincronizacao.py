"""Sincronização das tabelas oficiais (backlog, item 7): atualiza quando a versão muda, nunca apaga."""

from datetime import date

import pytest

from engines.contratos import Motor, VersaoInfo
from engines.models import ItemTabela, SincronizacaoTabela
from engines.sincronizacao import sincronizar_tabelas


class ClienteFalso:
    def __init__(self):
        self.versao_db = "V0073"
        self.chamadas = 0
        self.tabelas = {
            "situacoes_tributarias": [
                {
                    "codigo": "000",
                    "descricao": "Tributação integral",
                    "classificacoesTributarias": [{"codigo": "000001"}],
                },
                {
                    "codigo": "200",
                    "descricao": "Alíquota reduzida",
                    "classificacoesTributarias": [{"codigo": "200003"}],
                },
            ],
            "classificacoes_tributarias": [{"codigo": "000001", "descricao": "Integral", "pRedCBS": 0}],
            "ncm_vigentes": [{"ncm": "10063021", "descricao": "Arroz"}, {"ncm": "22030000", "descricao": "Cerveja"}],
            "nbs": [{"codigo": "122011200", "descricao": "Educação"}],
        }

    def versao(self):
        return VersaoInfo(Motor.REGIME_REGULAR, "1.7.2", self.versao_db, date(2026, 11, 9))

    def __getattr__(self, nome):
        if nome in self.__dict__.get("tabelas", {}):

            def baixar(data):
                self.chamadas += 1
                return self.tabelas[nome]

            return baixar
        raise AttributeError(nome)


@pytest.fixture
def cliente(db):
    return ClienteFalso()


def test_primeira_sincronizacao_copia_tudo(cliente):
    execucao = sincronizar_tabelas(cliente=cliente, data_referencia=date(2027, 1, 1))
    assert execucao.status == SincronizacaoTabela.Status.CONCLUIDA
    assert execucao.contagens["ncm"] == {"novos": 2, "alterados": 0, "iguais": 0, "removidos": 0}
    cst = ItemTabela.objects.get(tabela="cst", codigo="000")
    assert cst.dados["cClassTrib"] == ["000001"] and cst.versao_db_origem == "V0073"
    assert ItemTabela.objects.filter(vigente=True).count() == 6


def test_mesma_versao_nao_baixa_de_novo(cliente):
    sincronizar_tabelas(cliente=cliente)
    chamadas = cliente.chamadas
    assert sincronizar_tabelas(cliente=cliente) is None
    assert cliente.chamadas == chamadas


def test_versao_nova_guarda_historico(cliente):
    sincronizar_tabelas(cliente=cliente)
    cliente.versao_db = "V0074"
    cliente.tabelas["ncm_vigentes"] = [{"ncm": "10063021", "descricao": "Arroz semibranqueado"}]  # mudou e sumiu um

    execucao = sincronizar_tabelas(cliente=cliente)
    assert execucao.contagens["ncm"] == {"novos": 0, "alterados": 1, "iguais": 0, "removidos": 1}
    assert execucao.contagens["nbs"]["iguais"] == 1

    arroz = ItemTabela.objects.filter(tabela="ncm", codigo="10063021").order_by("criado_em")
    assert [(a.descricao, a.vigente) for a in arroz] == [("Arroz", False), ("Arroz semibranqueado", True)]
    cerveja = ItemTabela.objects.get(tabela="ncm", codigo="22030000")
    assert cerveja.vigente is False  # sumiu da tabela oficial, mas o registro continua
    assert ItemTabela.objects.get(tabela="nbs").versao_db_ultima == "V0074"


def test_conteudo_que_volta_reaproveita_registro(cliente):
    sincronizar_tabelas(cliente=cliente)
    original = list(cliente.tabelas["nbs"])
    cliente.tabelas["nbs"] = [{"codigo": "122011200", "descricao": "Educação (alterada)"}]
    sincronizar_tabelas(cliente=cliente, forcar=True)
    cliente.tabelas["nbs"] = original
    sincronizar_tabelas(cliente=cliente, forcar=True)
    registros = ItemTabela.objects.filter(tabela="nbs")
    assert registros.count() == 2
    assert registros.get(vigente=True).descricao == "Educação"


def test_tabela_vazia_falha_sem_mexer_no_que_existe(cliente):
    sincronizar_tabelas(cliente=cliente)
    cliente.versao_db = "V0074"
    cliente.tabelas["ncm_vigentes"] = []
    with pytest.raises(ValueError):
        sincronizar_tabelas(cliente=cliente)
    assert SincronizacaoTabela.objects.first().status == SincronizacaoTabela.Status.FALHOU
    assert ItemTabela.objects.filter(tabela="ncm", vigente=True).count() == 2
    assert ItemTabela.objects.get(tabela="nbs").versao_db_ultima == "V0073"
