"""Suíte cross-tenant: o escritório A nunca lê, edita ou apaga dado do escritório B.

Para cada rota de detalhe da API, um dono do escritório A tenta GET/PUT/PATCH/DELETE num objeto do
escritório B pela URL. Esperado: 404 (não revela que o objeto existe) e nada muda no banco.

`test_toda_rota_da_api_esta_na_matriz` falha quando alguém cria um endpoint novo e esquece de pô-lo aqui.
"""

import uuid

import pytest
from django.urls import reverse

from config.urls import router
from contas.models import Convite
from contas.papeis import Papel
from empresas.models import Empresa

METODOS_ESCRITA = ["put", "patch", "delete"]


@pytest.fixture
def cenario(criar_escritorio, criar_empresa, adicionar_membro, cliente_de, criar_simulacao):
    escritorio_a, _, dono_a = criar_escritorio("Escritório A")
    escritorio_b, _, dono_b = criar_escritorio("Escritório B")
    empresa_a = criar_empresa(escritorio_a, razao_social="Empresa do A")
    empresa_b = criar_empresa(escritorio_b, razao_social="Empresa do B")
    membro_b = adicionar_membro(escritorio_b, Papel.CONTADOR)
    convite_b = Convite(escritorio=escritorio_b, email="convidado@b.com.br", papel=Papel.CONTADOR)
    convite_b.gerar_token()
    convite_b.save()
    return {
        "cliente_a": cliente_de(dono_a),
        "dono_a": dono_a,
        "empresa_a": empresa_a,
        "objetos_b": {
            "empresa": empresa_b,
            "membro": membro_b,
            "convite": convite_b,
            "simulacao": criar_simulacao(empresa_b),
        },
        "escritorio_b": escritorio_b,
        "dono_b": dono_b,
    }


# basename do router → corpo válido para PUT/PATCH (um ataque bem formado, não um erro de validação).
CORPOS = {
    "empresa": {
        "cnpj": "11222333000181",
        "razao_social": "Invadida",
        "regime": "regular",
        "crt": 3,
        "cnae_principal": "6920601",
        "uf": "SP",
        "municipio_ibge": "3550308",
        "ativa": False,
    },
    "membro": {"papel": "leitura", "acesso_todas_empresas": True},
    "convite": {"email": "x@x.com.br", "papel": "leitura"},
    # Somente leitura: PUT/PATCH/DELETE têm de responder 405 (e não existir no Allow).
    "simulacao": {"rotulo": "Invadida"},
}


def test_toda_rota_da_api_esta_na_matriz():
    registradas = {basename for _, _, basename in router.registry}
    assert registradas == set(CORPOS), "Inclua o novo endpoint na suíte cross-tenant."


@pytest.mark.parametrize("basename", sorted(CORPOS))
def test_detalhe_de_outro_escritorio_responde_404(cenario, basename):
    alvo = cenario["objetos_b"][basename]
    url = reverse(f"{basename}-detail", args=[alvo.pk])
    cliente = cenario["cliente_a"]

    assert cliente.get(url).status_code == 404
    for metodo in METODOS_ESCRITA:
        resposta = getattr(cliente, metodo)(url, CORPOS[basename], format="json")
        assert resposta.status_code in (404, 405), (metodo, resposta.status_code, resposta.content)
        assert resposta.status_code != 405 or metodo not in _metodos_permitidos(cliente, url)


def _metodos_permitidos(cliente, url):
    resposta = cliente.options(url)
    return {m.strip().lower() for m in resposta.get("Allow", "").split(",")}


def test_objetos_de_b_continuam_intactos(cenario):
    cliente = cenario["cliente_a"]
    for basename, alvo in cenario["objetos_b"].items():
        url = reverse(f"{basename}-detail", args=[alvo.pk])
        for metodo in METODOS_ESCRITA:
            getattr(cliente, metodo)(url, CORPOS[basename], format="json")

    empresa_b = Empresa.objects.get(pk=cenario["objetos_b"]["empresa"].pk)
    assert empresa_b.ativa is True and empresa_b.razao_social == "Empresa do B"
    membro_b = cenario["objetos_b"]["membro"]
    membro_b.refresh_from_db()
    assert membro_b.papel == Papel.CONTADOR
    convite_b = Convite.objects.get(pk=cenario["objetos_b"]["convite"].pk)
    assert convite_b.cancelado_em is None


@pytest.mark.parametrize("basename", sorted(CORPOS))
def test_listagem_nao_mostra_dados_de_b(cenario, basename):
    resposta = cenario["cliente_a"].get(reverse(f"{basename}-list"))
    assert resposta.status_code == 200
    ids = {item["id"] for item in resposta.json()["results"]}
    assert str(cenario["objetos_b"][basename].pk) not in ids


def test_escolher_escritorio_alheio_pelo_cabecalho_e_negado(cenario):
    cliente = cenario["cliente_a"]
    cliente.credentials(HTTP_X_ESCRITORIO=str(cenario["escritorio_b"].pk))
    assert cliente.get(reverse("empresa-list")).status_code == 403
    assert cliente.get(reverse("escritorio-atual")).status_code == 403


def test_cabecalho_com_uuid_invalido_e_negado(cenario):
    cliente = cenario["cliente_a"]
    cliente.credentials(HTTP_X_ESCRITORIO="nao-e-uuid")
    assert cliente.get(reverse("empresa-list")).status_code == 403


def test_escritorio_inexistente_e_negado(cenario):
    cliente = cenario["cliente_a"]
    cliente.credentials(HTTP_X_ESCRITORIO=str(uuid.uuid4()))
    assert cliente.get(reverse("empresa-list")).status_code == 403


def test_selecionar_escritorio_alheio_na_sessao_responde_404(cenario):
    resposta = cenario["cliente_a"].post(
        reverse("auth-selecionar-escritorio"), {"escritorio": str(cenario["escritorio_b"].pk)}, format="json"
    )
    assert resposta.status_code == 404


def test_membro_restrito_nao_ve_empresa_fora_da_lista(criar_escritorio, criar_empresa, adicionar_membro, cliente_de):
    escritorio, _, _ = criar_escritorio()
    permitida = criar_empresa(escritorio, razao_social="Permitida")
    proibida = criar_empresa(escritorio, razao_social="Proibida")
    restrito = adicionar_membro(escritorio, Papel.CONTADOR, acesso_todas_empresas=False)
    permitida.membros_restritos.add(restrito)
    cliente = cliente_de(restrito)

    nomes = {e["razao_social"] for e in cliente.get(reverse("empresa-list")).json()["results"]}
    assert nomes == {"Permitida"}
    url = reverse("empresa-detail", args=[proibida.pk])
    assert cliente.get(url).status_code == 404
    assert cliente.patch(url, {"razao_social": "X"}, format="json").status_code == 404
    assert cliente.delete(url).status_code == 404


def test_membro_restrito_nao_concede_empresa_que_nao_ve(criar_escritorio, criar_empresa, adicionar_membro, cliente_de):
    """Mesmo um admin com acesso parcial não consegue dar acesso a empresa que ele não enxerga."""
    escritorio, _, _ = criar_escritorio()
    oculta = criar_empresa(escritorio)
    admin_restrito = adicionar_membro(escritorio, Papel.ADMIN, acesso_todas_empresas=False)
    alvo = adicionar_membro(escritorio, Papel.ASSISTENTE)
    resposta = cliente_de(admin_restrito).patch(
        reverse("membro-detail", args=[alvo.pk]),
        {"acesso_todas_empresas": False, "empresas": [str(oculta.pk)]},
        format="json",
    )
    assert resposta.status_code == 400


def test_convite_nao_pode_apontar_empresa_de_outro_escritorio(cenario):
    resposta = cenario["cliente_a"].post(
        reverse("convite-list"),
        {
            "email": "novo@a.com.br",
            "papel": "assistente",
            "acesso_todas_empresas": False,
            "empresas": [str(cenario["objetos_b"]["empresa"].pk)],
        },
        format="json",
    )
    assert resposta.status_code == 400


def test_sem_login_nada_e_acessivel(cenario):
    from rest_framework.test import APIClient

    anonimo = APIClient()
    for basename in CORPOS:
        assert anonimo.get(reverse(f"{basename}-list")).status_code == 403
    assert anonimo.get(reverse("escritorio-atual")).status_code == 403
