"""Tabela de alíquotas nominais (decisão D2, backlog item 8)."""

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from engines import aliquotas
from engines.models import AliquotaNominal

FONTE = "LC 214/2025, art. 346 (teste)"


@pytest.fixture
def cadastrar(db):
    def _cadastrar(esfera, codigo, valor, inicio, fim=None, fonte=FONTE):
        return AliquotaNominal.objects.create(
            esfera=esfera,
            codigo_ibge=codigo,
            aliquota=Decimal(valor),
            vigencia_inicio=inicio,
            vigencia_fim=fim,
            fonte=fonte,
            data_publicacao=date(2026, 1, 1),
        )

    return _cadastrar


def test_nao_aceita_registro_sem_fonte(cadastrar):
    with pytest.raises(ValidationError) as exc:
        cadastrar("uniao", "", "8.8", date(2027, 1, 1), fonte="   ")
    assert "fonte" in exc.value.message_dict


def test_banco_tambem_barra_registro_sem_fonte(db):
    """Mesmo quem passar por fora do modelo (bulk, SQL) esbarra na constraint."""
    with pytest.raises(IntegrityError), transaction.atomic():
        AliquotaNominal.objects.bulk_create(
            [
                AliquotaNominal(
                    esfera="uniao",
                    aliquota=Decimal("8.8"),
                    vigencia_inicio=date(2027, 1, 1),
                    fonte="",
                    data_publicacao=date(2026, 1, 1),
                )
            ]
        )


@pytest.mark.parametrize(("esfera", "codigo"), [("uniao", "35"), ("uf", "3550308"), ("municipio", "35"), ("uf", "SP")])
def test_codigo_ibge_tem_que_combinar_com_a_esfera(cadastrar, esfera, codigo):
    with pytest.raises(ValidationError) as exc:
        cadastrar(esfera, codigo, "1", date(2027, 1, 1))
    assert "codigo_ibge" in exc.value.message_dict


def test_nao_edita_nem_apaga(cadastrar):
    registro = cadastrar("uniao", "", "8.8", date(2027, 1, 1))
    registro.aliquota = Decimal("9")
    with pytest.raises(ValidationError):
        registro.save()
    with pytest.raises(ValidationError):
        registro.delete()
    assert AliquotaNominal.objects.get(pk=registro.pk).aliquota == Decimal("8.8")


def test_consulta_por_data_devolve_a_vigente(cadastrar):
    antiga = cadastrar("uf", "35", "0.05", date(2027, 1, 1), fim=date(2028, 12, 31))
    nova = cadastrar("uf", "35", "0.10", date(2029, 1, 1))
    assert AliquotaNominal.vigente("uf", date(2027, 6, 1), "35") == antiga
    assert AliquotaNominal.vigente("uf", date(2028, 12, 31), "35") == antiga
    assert AliquotaNominal.vigente("uf", date(2029, 1, 1), "35") == nova
    assert AliquotaNominal.vigente("uf", date(2026, 12, 31), "35") is None
    assert AliquotaNominal.vigente("uf", date(2027, 6, 1), "43") is None


def test_correcao_e_registro_novo_e_vale_o_ultimo(cadastrar):
    cadastrar("uniao", "", "8.8", date(2027, 1, 1))
    corrigida = cadastrar("uniao", "", "8.7", date(2027, 1, 1), fonte="Errata da LC (teste)")
    assert AliquotaNominal.vigente("uniao", date(2027, 3, 1)) == corrigida
    assert AliquotaNominal.objects.count() == 2


def test_resolver_marca_origem_de_cada_aliquota(cadastrar):
    cbs = cadastrar("uniao", "", "8.8", date(2027, 1, 1))
    cadastrar("uf", "43", "0.05", date(2027, 1, 1))
    propostas = aliquotas.propostas_da_tabela(date(2027, 3, 10), "4314902")

    # Contador edita o IBS estadual, informa o municipal (sem cadastro) e deixa a CBS da tabela.
    valores, origens, faltando = aliquotas.resolver(
        propostas, {"ibs_estadual": Decimal("0.06"), "ibs_municipal": Decimal("0.05")}
    )
    assert faltando == []
    assert valores == {"cbs": Decimal("8.8"), "ibs_estadual": Decimal("0.06"), "ibs_municipal": Decimal("0.05")}
    assert origens["cbs"]["origem"] == "tabela_tributo360"
    assert origens["cbs"]["aliquota_nominal_id"] == cbs.pk and origens["cbs"]["fonte"] == FONTE
    assert origens["ibs_estadual"] == {
        "origem": "editada_pelo_usuario",
        "valor": "0.06",
        "valor_anterior": "0.0500",
        "aliquota_nominal_id": propostas["ibs_estadual"].registro.pk,
    }
    assert origens["ibs_municipal"]["origem"] == "editada_pelo_usuario"
    assert origens["ibs_municipal"]["valor_anterior"] is None


def test_resolver_aponta_o_que_falta(db):
    propostas = aliquotas.propostas_da_tabela(date(2027, 3, 10), "4314902")
    _, _, faltando = aliquotas.resolver(propostas, {"cbs": Decimal("8.8")})
    assert faltando == ["ibs_estadual", "ibs_municipal"]


def test_valor_igual_ao_da_tabela_conta_como_tabela(cadastrar):
    cadastrar("uniao", "", "8.8", date(2027, 1, 1))
    propostas = aliquotas.propostas_da_tabela(date(2027, 3, 10), "4314902")
    _, origens, _ = aliquotas.resolver(propostas, {"cbs": Decimal("8.80")})
    assert origens["cbs"]["origem"] == "tabela_tributo360"


def test_ano_de_corte():
    assert not aliquotas.exige_aliquotas(date(2026, 12, 31))
    assert aliquotas.exige_aliquotas(date(2027, 1, 1))


def test_endpoint_de_aliquotas_vigentes(cadastrar, criar_escritorio, cliente_de):
    _, _, dono = criar_escritorio()
    cliente = cliente_de(dono)
    cadastrar("uniao", "", "8.8", date(2027, 1, 1))

    corpo = cliente.get("/api/aliquotas-nominais/vigentes/", {"data": "2027-03-10", "municipio": "4314902"}).json()
    assert corpo["exige_aliquotas"] is True
    assert corpo["aliquotas"]["cbs"]["aliquota"] == "8.8000"
    assert corpo["aliquotas"]["cbs"]["fonte"] == FONTE
    assert corpo["aliquotas"]["ibs_estadual"] is None

    corpo = cliente.get("/api/aliquotas-nominais/vigentes/", {"data": "2026-06-01", "municipio": "4314902"}).json()
    assert corpo["exige_aliquotas"] is False

    assert (
        cliente.get("/api/aliquotas-nominais/vigentes/", {"data": "2027-01-01", "municipio": "43"}).status_code == 400
    )


@pytest.mark.django_db
def test_constraint_de_fonte_existe_no_banco():
    """Confere que a constraint existe no banco (protege contra migração apagada por engano)."""
    with connection.cursor() as cursor:
        nomes = connection.introspection.get_constraints(cursor, AliquotaNominal._meta.db_table)
    assert "aliquota_nominal_exige_fonte" in nomes
