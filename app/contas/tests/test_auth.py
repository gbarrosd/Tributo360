import re

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework.test import APIClient

from auditoria.models import Auditoria
from contas.models import Assinatura, Escritorio, Membership, Usuario
from contas.papeis import Papel

CADASTRO = {
    "nome": "Ana Contadora",
    "email": "Ana@Escritorio.com.br",
    "senha": "Balanco2027ok",
    "escritorio_razao_social": "Ana Contabilidade Ltda",
    "escritorio_documento": "11.222.333/0001-81",
}


@pytest.fixture
def api():
    return APIClient(enforce_csrf_checks=True)


def _csrf(api):
    api.get(reverse("auth-csrf"))
    return {"HTTP_X_CSRFTOKEN": api.cookies["csrftoken"].value}


@pytest.mark.django_db
class TestCadastro:
    def test_cria_usuario_escritorio_assinatura_e_dono(self, api):
        resposta = api.post(reverse("auth-cadastro"), CADASTRO, format="json", **_csrf(api))
        assert resposta.status_code == 201, resposta.content
        usuario = Usuario.objects.get()
        assert usuario.email == "ana@escritorio.com.br"
        escritorio = Escritorio.objects.get()
        assert escritorio.documento == "11222333000181"
        assert escritorio.status == Escritorio.Status.TRIAL
        assinatura = Assinatura.objects.get(escritorio=escritorio)
        assert assinatura.plano == "trial" and assinatura.fim is not None
        membership = Membership.objects.get()
        assert membership.papel == Papel.DONO and membership.usuario == usuario
        assert resposta.json()["escritorio_atual"]["papel"] == "dono"
        assert Auditoria.objects.filter(acao="escritorio.criar", escritorio=escritorio).exists()

        # Já sai logado e com o escritório selecionado.
        assert api.get(reverse("escritorio-atual")).json()["razao_social"] == "Ana Contabilidade Ltda"

    @pytest.mark.parametrize(
        "senha",
        ["curta1", "somenteletrasaqui", "12345678901234", "password123", "anacontadora1"],
    )
    def test_politica_de_senha(self, api, senha):
        resposta = api.post(reverse("auth-cadastro"), {**CADASTRO, "senha": senha}, format="json", **_csrf(api))
        assert resposta.status_code == 400
        assert "senha" in resposta.json() or "non_field_errors" in resposta.json()
        assert not Usuario.objects.exists()

    def test_email_duplicado_sem_diferenciar_maiusculas(self, api, criar_usuario):
        criar_usuario(email="ana@escritorio.com.br")
        resposta = api.post(reverse("auth-cadastro"), CADASTRO, format="json", **_csrf(api))
        assert resposta.status_code == 400
        assert "email" in resposta.json()

    def test_documento_invalido(self, api):
        resposta = api.post(
            reverse("auth-cadastro"), {**CADASTRO, "escritorio_documento": "123"}, format="json", **_csrf(api)
        )
        assert resposta.status_code == 400
        assert "escritorio_documento" in resposta.json()


@pytest.mark.django_db
class TestLogin:
    def test_login_logout(self, api, criar_escritorio, criar_usuario):
        usuario = criar_usuario(email="bia@exemplo.com.br")
        criar_escritorio(dono=usuario)
        resposta = api.post(
            reverse("auth-login"),
            {"email": "BIA@exemplo.com.br", "senha": "SenhaForte123"},
            format="json",
            **_csrf(api),
        )
        assert resposta.status_code == 200, resposta.content
        assert resposta.json()["usuario"]["email"] == "bia@exemplo.com.br"
        assert resposta.json()["escritorio_atual"] is not None
        assert api.get(reverse("auth-eu")).status_code == 200

        assert api.post(reverse("auth-logout"), **_csrf(api)).status_code == 204
        assert api.get(reverse("auth-eu")).status_code == 403

    def test_senha_errada(self, api, criar_usuario):
        criar_usuario(email="bia@exemplo.com.br")
        resposta = api.post(
            reverse("auth-login"), {"email": "bia@exemplo.com.br", "senha": "errada123456"}, format="json", **_csrf(api)
        )
        assert resposta.status_code == 400

    def test_usuario_inativo_nao_entra(self, api, criar_usuario):
        usuario = criar_usuario(email="bia@exemplo.com.br")
        usuario.is_active = False
        usuario.save()
        resposta = api.post(
            reverse("auth-login"),
            {"email": "bia@exemplo.com.br", "senha": "SenhaForte123"},
            format="json",
            **_csrf(api),
        )
        assert resposta.status_code == 400

    def test_login_tem_limite_de_tentativas(self, api, criar_usuario, settings):
        criar_usuario(email="bia@exemplo.com.br")
        cabecalho = _csrf(api)
        codigos = [
            api.post(
                reverse("auth-login"), {"email": "bia@exemplo.com.br", "senha": "x"}, format="json", **cabecalho
            ).status_code
            for _ in range(12)
        ]
        assert codigos[-1] == 429

    def test_quem_tem_dois_escritorios_escolhe(self, criar_escritorio, criar_usuario, adicionar_membro):
        usuario = criar_usuario()
        escritorio_a, _, _ = criar_escritorio("A", dono=usuario)
        escritorio_b, _, _ = criar_escritorio("B")
        adicionar_membro(escritorio_b, Papel.LEITURA, usuario=usuario)
        api = APIClient()
        resposta = api.post(reverse("auth-login"), {"email": usuario.email, "senha": "SenhaForte123"}, format="json")
        assert resposta.json()["escritorio_atual"] is None
        assert len(resposta.json()["escritorios"]) == 2
        assert api.get(reverse("empresa-list")).status_code == 403

        api.post(reverse("auth-selecionar-escritorio"), {"escritorio": str(escritorio_b.pk)}, format="json")
        assert api.get(reverse("escritorio-atual")).json()["razao_social"] == "B"
        # O cabeçalho tem precedência sobre a sessão.
        api.credentials(HTTP_X_ESCRITORIO=str(escritorio_a.pk))
        assert api.get(reverse("escritorio-atual")).json()["razao_social"] == "A"


@pytest.mark.django_db
class TestSenha:
    def test_reset_por_email(self, criar_usuario):
        criar_usuario(email="bia@exemplo.com.br")
        api = APIClient()
        assert (
            api.post(reverse("auth-esqueci-senha"), {"email": "bia@exemplo.com.br"}, format="json").status_code == 204
        )
        assert len(mail.outbox) == 1
        uid, token = re.search(r"uid=([^&\s]+)&token=(\S+)", mail.outbox[0].body).groups()

        fraca = api.post(
            reverse("auth-redefinir-senha"), {"uid": uid, "token": token, "nova_senha": "123"}, format="json"
        )
        assert fraca.status_code == 400 and "nova_senha" in fraca.json()

        ok = api.post(
            reverse("auth-redefinir-senha"), {"uid": uid, "token": token, "nova_senha": "NovaSenha2027"}, format="json"
        )
        assert ok.status_code == 204
        assert Usuario.objects.get().check_password("NovaSenha2027")

        # O token não vale duas vezes (a senha mudou).
        de_novo = api.post(
            reverse("auth-redefinir-senha"), {"uid": uid, "token": token, "nova_senha": "OutraSenha2027"}, format="json"
        )
        assert de_novo.status_code == 400

    def test_email_desconhecido_nao_revela_nada(self, db):
        resposta = APIClient().post(reverse("auth-esqueci-senha"), {"email": "ninguem@exemplo.com.br"}, format="json")
        assert resposta.status_code == 204
        assert mail.outbox == []

    def test_uid_lixo(self, db):
        resposta = APIClient().post(
            reverse("auth-redefinir-senha"), {"uid": "lixo", "token": "x", "nova_senha": "NovaSenha2027"}, format="json"
        )
        assert resposta.status_code == 400

    def test_alterar_senha(self, criar_usuario):
        usuario = criar_usuario()
        api = APIClient()
        api.force_login(usuario)
        errada = api.post(
            reverse("auth-alterar-senha"), {"senha_atual": "x", "nova_senha": "NovaSenha2027"}, format="json"
        )
        assert errada.status_code == 400
        certa = api.post(
            reverse("auth-alterar-senha"),
            {"senha_atual": "SenhaForte123", "nova_senha": "NovaSenha2027"},
            format="json",
        )
        assert certa.status_code == 204
        assert api.get(reverse("auth-eu")).status_code == 200  # continua logado
