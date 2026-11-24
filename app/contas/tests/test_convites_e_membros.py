import re
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from auditoria.models import Auditoria
from contas.models import Convite, Membership, Usuario
from contas.papeis import Papel


def _token_do_email():
    return re.search(r"token=(\S+)", mail.outbox[-1].body).group(1)


@pytest.fixture
def escritorio(criar_escritorio):
    return criar_escritorio("Escritório Convites")


@pytest.mark.django_db(transaction=True)
class TestConvites:
    def test_fluxo_completo_com_conta_nova(self, escritorio, cliente_de, criar_empresa):
        esc, _, dono = escritorio
        empresa = criar_empresa(esc)
        resposta = cliente_de(dono).post(
            reverse("convite-list"),
            {
                "email": "Novo@Exemplo.com.br",
                "papel": "assistente",
                "acesso_todas_empresas": False,
                "empresas": [str(empresa.pk)],
            },
            format="json",
        )
        assert resposta.status_code == 201, resposta.content
        assert "token" not in str(resposta.json())
        assert mail.outbox[-1].to == ["novo@exemplo.com.br"]
        token = _token_do_email()
        assert Convite.objects.get().token_hash != token  # só o hash fica no banco

        anonimo = APIClient()
        info = anonimo.get(reverse("convite-consultar"), {"token": token}).json()
        assert info == {
            "escritorio": esc.razao_social,
            "email": "novo@exemplo.com.br",
            "papel": "assistente",
            "conta_existente": False,
        }

        sem_senha = anonimo.post(reverse("convite-aceitar"), {"token": token, "nome": "Novo"}, format="json")
        assert sem_senha.status_code == 400

        aceito = anonimo.post(
            reverse("convite-aceitar"), {"token": token, "nome": "Novo", "senha": "Assistente2027"}, format="json"
        )
        assert aceito.status_code == 201, aceito.content
        membership = Membership.objects.get(usuario__email="novo@exemplo.com.br")
        assert membership.papel == Papel.ASSISTENTE
        assert not membership.acesso_todas_empresas
        assert list(membership.empresas_permitidas.all()) == [empresa]
        assert anonimo.get(reverse("empresa-list")).json()["count"] == 1  # já entrou logado

        # Convite usado não serve de novo.
        assert anonimo.post(reverse("convite-aceitar"), {"token": token}, format="json").status_code == 404
        acoes = set(Auditoria.objects.values_list("acao", flat=True))
        assert {"convite.criar", "convite.aceitar"} <= acoes

    def test_conta_existente_precisa_logar(self, escritorio, cliente_de, criar_usuario):
        _, _, dono = escritorio
        existente = criar_usuario(email="ja@exemplo.com.br")
        cliente_de(dono).post(
            reverse("convite-list"), {"email": "ja@exemplo.com.br", "papel": "contador"}, format="json"
        )
        token = _token_do_email()

        anonimo = APIClient()
        assert anonimo.post(reverse("convite-aceitar"), {"token": token}, format="json").status_code == 409

        logado = APIClient()
        logado.force_login(existente)
        assert logado.post(reverse("convite-aceitar"), {"token": token}, format="json").status_code == 201

    def test_convite_de_outro_email_e_recusado(self, escritorio, cliente_de, criar_usuario):
        _, _, dono = escritorio
        cliente_de(dono).post(
            reverse("convite-list"), {"email": "certo@exemplo.com.br", "papel": "contador"}, format="json"
        )
        token = _token_do_email()
        intruso = APIClient()
        intruso.force_login(criar_usuario(email="outro@exemplo.com.br"))
        assert intruso.post(reverse("convite-aceitar"), {"token": token}, format="json").status_code == 403
        assert not Membership.objects.filter(usuario__email="outro@exemplo.com.br").exists()

    def test_convite_expirado(self, escritorio, cliente_de):
        _, _, dono = escritorio
        cliente_de(dono).post(
            reverse("convite-list"), {"email": "x@exemplo.com.br", "papel": "contador"}, format="json"
        )
        token = _token_do_email()
        Convite.objects.update(expira_em=timezone.now() - timedelta(minutes=1))
        resposta = APIClient().post(
            reverse("convite-aceitar"), {"token": token, "nome": "X", "senha": "Contador2027"}, format="json"
        )
        assert resposta.status_code == 404
        # Um convite vencido não impede convidar de novo.
        novo = cliente_de(dono).post(
            reverse("convite-list"), {"email": "x@exemplo.com.br", "papel": "contador"}, format="json"
        )
        assert novo.status_code == 201

    def test_cancelar_convite(self, escritorio, cliente_de):
        _, _, dono = escritorio
        cliente = cliente_de(dono)
        criado = cliente.post(
            reverse("convite-list"), {"email": "x@exemplo.com.br", "papel": "contador"}, format="json"
        )
        token = _token_do_email()
        assert cliente.delete(reverse("convite-detail", args=[criado.json()["id"]])).status_code == 204
        assert APIClient().get(reverse("convite-consultar"), {"token": token}).status_code == 404

    def test_duplicado_e_membro_existente(self, escritorio, cliente_de, adicionar_membro):
        esc, _, dono = escritorio
        cliente = cliente_de(dono)
        cliente.post(reverse("convite-list"), {"email": "x@exemplo.com.br", "papel": "contador"}, format="json")
        assert (
            cliente.post(
                reverse("convite-list"), {"email": "X@exemplo.com.br", "papel": "leitura"}, format="json"
            ).status_code
            == 409
        )
        membro = adicionar_membro(esc)
        assert (
            cliente.post(
                reverse("convite-list"), {"email": membro.usuario.email, "papel": "leitura"}, format="json"
            ).status_code
            == 409
        )

    def test_limite_de_usuarios_do_plano_conta_convites_pendentes(self, escritorio, cliente_de):
        esc, _, dono = escritorio  # trial: 3 usuários
        cliente = cliente_de(dono)
        for n in (1, 2):
            assert (
                cliente.post(
                    reverse("convite-list"), {"email": f"p{n}@exemplo.com.br", "papel": "leitura"}, format="json"
                ).status_code
                == 201
            )
        estourou = cliente.post(
            reverse("convite-list"), {"email": "p3@exemplo.com.br", "papel": "leitura"}, format="json"
        )
        assert estourou.status_code == 403
        assert estourou.json()["detail"].startswith("O plano permite 3 usuários")

    @pytest.mark.parametrize("papel", [Papel.CONTADOR, Papel.ASSISTENTE, Papel.LEITURA])
    def test_so_dono_e_admin_convidam(self, escritorio, cliente_de, adicionar_membro, papel):
        esc, _, _ = escritorio
        membro = adicionar_membro(esc, papel)
        resposta = cliente_de(membro).post(
            reverse("convite-list"), {"email": "x@exemplo.com.br", "papel": "leitura"}, format="json"
        )
        assert resposta.status_code == 403

    def test_admin_nao_convida_dono(self, escritorio, cliente_de, adicionar_membro):
        esc, _, _ = escritorio
        admin = adicionar_membro(esc, Papel.ADMIN)
        resposta = cliente_de(admin).post(
            reverse("convite-list"), {"email": "x@exemplo.com.br", "papel": "dono"}, format="json"
        )
        assert resposta.status_code == 400


@pytest.mark.django_db
class TestMembros:
    def test_dono_muda_papel_e_restringe_empresas(self, escritorio, cliente_de, adicionar_membro, criar_empresa):
        esc, _, dono = escritorio
        empresa = criar_empresa(esc)
        membro = adicionar_membro(esc, Papel.CONTADOR)
        url = reverse("membro-detail", args=[membro.pk])
        resposta = cliente_de(dono).patch(
            url, {"papel": "assistente", "acesso_todas_empresas": False, "empresas": [str(empresa.pk)]}, format="json"
        )
        assert resposta.status_code == 200, resposta.content
        assert resposta.json()["empresas_permitidas"] == [str(empresa.pk)]
        registro = Auditoria.objects.get(acao="membro.editar")
        assert registro.antes["papel"] == "contador" and registro.depois["papel"] == "assistente"
        assert registro.depois["empresas"] == [str(empresa.pk)]

        # Voltar para acesso total limpa a lista.
        cliente_de(dono).patch(url, {"acesso_todas_empresas": True}, format="json")
        membro.refresh_from_db()
        assert not membro.empresas_permitidas.exists()

    def test_ultimo_dono_nao_sai_nem_e_rebaixado(self, escritorio, cliente_de):
        _, _, dono = escritorio
        url = reverse("membro-detail", args=[dono.pk])
        assert cliente_de(dono).patch(url, {"papel": "admin"}, format="json").status_code == 400
        assert cliente_de(dono).delete(url).status_code == 400

    def test_com_dois_donos_um_pode_sair(self, escritorio, cliente_de, adicionar_membro):
        esc, _, dono = escritorio
        adicionar_membro(esc, Papel.DONO)
        assert cliente_de(dono).delete(reverse("membro-detail", args=[dono.pk])).status_code == 204

    def test_admin_nao_mexe_em_dono_nem_promove_a_dono(self, escritorio, cliente_de, adicionar_membro):
        esc, _, dono = escritorio
        admin = adicionar_membro(esc, Papel.ADMIN)
        contador = adicionar_membro(esc, Papel.CONTADOR)
        cliente = cliente_de(admin)
        assert (
            cliente.patch(reverse("membro-detail", args=[dono.pk]), {"papel": "leitura"}, format="json").status_code
            == 403
        )
        assert cliente.delete(reverse("membro-detail", args=[dono.pk])).status_code == 403
        assert (
            cliente.patch(reverse("membro-detail", args=[contador.pk]), {"papel": "dono"}, format="json").status_code
            == 403
        )
        assert (
            cliente.patch(reverse("membro-detail", args=[contador.pk]), {"papel": "leitura"}, format="json").status_code
            == 200
        )

    def test_remover_membro_corta_acesso(self, escritorio, cliente_de, adicionar_membro):
        esc, _, dono = escritorio
        membro = adicionar_membro(esc, Papel.CONTADOR)
        cliente_membro = cliente_de(membro)
        assert cliente_membro.get(reverse("empresa-list")).status_code == 200
        assert cliente_de(dono).delete(reverse("membro-detail", args=[membro.pk])).status_code == 204
        assert cliente_membro.get(reverse("empresa-list")).status_code == 403
        assert Usuario.objects.filter(pk=membro.usuario_id).exists()  # a conta continua existindo
        assert Auditoria.objects.filter(acao="membro.remover").exists()

    def test_leitura_ve_lista_mas_nao_edita(self, escritorio, cliente_de, adicionar_membro):
        esc, _, dono = escritorio
        leitor = adicionar_membro(esc, Papel.LEITURA)
        cliente = cliente_de(leitor)
        assert cliente.get(reverse("membro-list")).json()["count"] == 2
        assert (
            cliente.patch(reverse("membro-detail", args=[dono.pk]), {"papel": "leitura"}, format="json").status_code
            == 403
        )
        assert cliente.get(reverse("convite-list")).status_code == 403


@pytest.mark.django_db
class TestEscritorio:
    def test_so_dono_edita_dados_do_escritorio(self, escritorio, cliente_de, adicionar_membro):
        esc, _, dono = escritorio
        admin = adicionar_membro(esc, Papel.ADMIN)
        assert (
            cliente_de(admin).patch(reverse("escritorio-atual"), {"razao_social": "X"}, format="json").status_code
            == 403
        )
        resposta = cliente_de(dono).patch(reverse("escritorio-atual"), {"razao_social": "Novo Nome"}, format="json")
        assert resposta.status_code == 200
        assert resposta.json()["assinatura"]["limite_empresas"] == 10
        assert Auditoria.objects.filter(acao="escritorio.editar").exists()

    def test_documento_invalido(self, escritorio, cliente_de):
        _, _, dono = escritorio
        resposta = cliente_de(dono).patch(reverse("escritorio-atual"), {"documento": "123"}, format="json")
        assert resposta.status_code == 400
        com_mascara = cliente_de(dono).patch(
            reverse("escritorio-atual"), {"documento": "529.982.247-25"}, format="json"
        )
        assert com_mascara.status_code == 200
        assert com_mascara.json()["documento"] == "52998224725"
