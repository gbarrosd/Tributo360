import pytest
from django.urls import reverse

from auditoria.models import Auditoria
from contas.papeis import Papel
from empresas.models import Empresa

NOVA = {
    "cnpj": "12.ABC.345/01DE-35",
    "razao_social": "Padaria Alfa Ltda",
    "nome_fantasia": "Padaria Alfa",
    "regime": "simples",
    "crt": 1,
    "cnae_principal": "1091-1/02",
    "uf": "RJ",
    "municipio_ibge": "3304557",
}


@pytest.fixture
def dono(criar_escritorio):
    return criar_escritorio("Escritório Empresas")[2]


@pytest.mark.django_db
class TestCadastro:
    def test_cria_normaliza_e_audita(self, dono, cliente_de):
        resposta = cliente_de(dono).post(reverse("empresa-list"), NOVA, format="json")
        assert resposta.status_code == 201, resposta.content
        corpo = resposta.json()
        assert corpo["cnpj"] == "12ABC34501DE35"
        assert corpo["cnae_principal"] == "1091102"
        empresa = Empresa.objects.get()
        assert empresa.escritorio_id == dono.escritorio_id
        registro = Auditoria.objects.get(acao="empresa.criar")
        assert registro.usuario == dono.usuario
        assert registro.objeto_id == str(empresa.pk)
        assert registro.depois["razao_social"] == "Padaria Alfa Ltda"
        assert registro.antes is None

    @pytest.mark.parametrize(
        ("campo", "valor"),
        [
            ("cnpj", "11222333000182"),
            ("cnae_principal", "123"),
            ("uf", "XX"),
            ("municipio_ibge", "3550308"),  # São Paulo não é do RJ
            ("crt", 3),  # CRT de regime normal numa empresa do Simples
            ("regime", "lucro_presumido"),
        ],
    )
    def test_validacoes(self, dono, cliente_de, campo, valor):
        resposta = cliente_de(dono).post(reverse("empresa-list"), {**NOVA, campo: valor}, format="json")
        assert resposta.status_code == 400
        assert campo in resposta.json()

    def test_cnpj_unico_no_escritorio_mas_livre_entre_escritorios(self, dono, cliente_de, criar_escritorio):
        assert cliente_de(dono).post(reverse("empresa-list"), NOVA, format="json").status_code == 201
        repetida = cliente_de(dono).post(reverse("empresa-list"), {**NOVA, "cnpj": "12abc34501de35"}, format="json")
        assert repetida.status_code == 400 and "cnpj" in repetida.json()

        outro_dono = criar_escritorio("Outro")[2]
        assert cliente_de(outro_dono).post(reverse("empresa-list"), NOVA, format="json").status_code == 201

    def test_cnpj_nao_muda_na_edicao(self, dono, cliente_de, criar_empresa):
        empresa = criar_empresa(dono.escritorio)
        resposta = cliente_de(dono).patch(
            reverse("empresa-detail", args=[empresa.pk]), {"cnpj": "11222333000181"}, format="json"
        )
        assert resposta.status_code == 400

    def test_edicao_valida_contra_dados_atuais(self, dono, cliente_de, criar_empresa):
        empresa = criar_empresa(dono.escritorio)  # regular, CRT 3
        url = reverse("empresa-detail", args=[empresa.pk])
        assert cliente_de(dono).patch(url, {"regime": "simples"}, format="json").status_code == 400
        assert cliente_de(dono).patch(url, {"regime": "simples", "crt": 1}, format="json").status_code == 200

    def test_edicao_audita_antes_e_depois(self, dono, cliente_de, criar_empresa):
        empresa = criar_empresa(dono.escritorio, razao_social="Antiga")
        cliente_de(dono).patch(reverse("empresa-detail", args=[empresa.pk]), {"razao_social": "Nova"}, format="json")
        registro = Auditoria.objects.get(acao="empresa.editar")
        assert registro.antes["razao_social"] == "Antiga"
        assert registro.depois["razao_social"] == "Nova"

    def test_delete_inativa_sem_apagar(self, dono, cliente_de, criar_empresa):
        empresa = criar_empresa(dono.escritorio)
        assert cliente_de(dono).delete(reverse("empresa-detail", args=[empresa.pk])).status_code == 204
        empresa.refresh_from_db()
        assert empresa.ativa is False
        assert Auditoria.objects.filter(acao="empresa.inativar").exists()

    def test_busca_e_filtro(self, dono, cliente_de, criar_empresa):
        criar_empresa(dono.escritorio, razao_social="Mercado Beta")
        criar_empresa(dono.escritorio, razao_social="Oficina Gama", ativa=False)
        cliente = cliente_de(dono)
        assert cliente.get(reverse("empresa-list"), {"q": "beta"}).json()["count"] == 1
        assert cliente.get(reverse("empresa-list"), {"ativa": "false"}).json()["count"] == 1


@pytest.mark.django_db
class TestLimiteDoPlano:
    def test_limite_de_empresas_ativas(self, dono, cliente_de, criar_empresa):
        for _ in range(10):  # trial: 10 empresas ativas
            criar_empresa(dono.escritorio)
        resposta = cliente_de(dono).post(reverse("empresa-list"), NOVA, format="json")
        assert resposta.status_code == 403
        assert "10 empresas ativas" in resposta.json()["detail"]

        # Inativa já não conta, e pode ser cadastrada mesmo no limite.
        assert (
            cliente_de(dono).post(reverse("empresa-list"), {**NOVA, "ativa": False}, format="json").status_code == 201
        )

    def test_reativar_respeita_limite(self, dono, cliente_de, criar_empresa):
        inativa = criar_empresa(dono.escritorio, ativa=False)
        for _ in range(10):
            criar_empresa(dono.escritorio)
        resposta = cliente_de(dono).patch(reverse("empresa-detail", args=[inativa.pk]), {"ativa": True}, format="json")
        assert resposta.status_code == 403
        inativa.refresh_from_db()
        assert inativa.ativa is False


@pytest.mark.django_db
class TestPapeis:
    @pytest.mark.parametrize(
        ("papel", "pode_editar"),
        [
            (Papel.DONO, True),
            (Papel.ADMIN, True),
            (Papel.CONTADOR, True),
            (Papel.ASSISTENTE, False),
            (Papel.LEITURA, False),
        ],
    )
    def test_matriz(self, dono, cliente_de, adicionar_membro, criar_empresa, papel, pode_editar):
        empresa = criar_empresa(dono.escritorio)
        membro = dono if papel == Papel.DONO else adicionar_membro(dono.escritorio, papel)
        cliente = cliente_de(membro)
        assert cliente.get(reverse("empresa-list")).status_code == 200
        assert cliente.get(reverse("empresa-detail", args=[empresa.pk])).status_code == 200

        esperado = (201, 200) if pode_editar else (403, 403)
        assert cliente.post(reverse("empresa-list"), NOVA, format="json").status_code == esperado[0]
        assert (
            cliente.patch(
                reverse("empresa-detail", args=[empresa.pk]), {"razao_social": "X"}, format="json"
            ).status_code
            == esperado[1]
        )

    def test_contador_restrito_continua_vendo_o_que_cadastrou(self, dono, cliente_de, adicionar_membro):
        restrito = adicionar_membro(dono.escritorio, Papel.CONTADOR, acesso_todas_empresas=False)
        cliente = cliente_de(restrito)
        criada = cliente.post(reverse("empresa-list"), NOVA, format="json")
        assert criada.status_code == 201
        assert cliente.get(reverse("empresa-detail", args=[criada.json()["id"]])).status_code == 200
