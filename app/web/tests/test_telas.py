"""Telas web: login, escolha de escritório na sessão, cadastros, simulação pelo formulário e comparação."""

import io
import re

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from catalogo.models import ItemCatalogo
from conftest import SENHA
from contas.models import Escritorio, Membership, Usuario
from contas.papeis import Papel
from empresas.models import Empresa
from engines.models import VersaoMotor
from simulacoes.models import AliquotaEfetivaSN, Simulacao

MOTOR_RR = "/api/calculadora/observabilidade/regime-geral"
MOTOR_SN = "/api/calculadora/observabilidade/simples-nacional/nao-assistido"


@pytest.fixture
def cenario(criar_escritorio, criar_empresa):
    escritorio, dono, membership = criar_escritorio("Contábil Exemplo")
    empresa = criar_empresa(escritorio, razao_social="Mercado Bom Preço", uf="RS", municipio_ibge="4314902")
    simples = criar_empresa(escritorio, razao_social="Padaria Simples", regime="simples", crt=1)
    return {"escritorio": escritorio, "dono": dono, "membership": membership, "empresa": empresa, "simples": simples}


def erros_na_pagina(resposta):
    return re.findall(r"(?:invalid-feedback|alert-danger)[^<]*<?[^<]*", resposta.content.decode())


def navegador_de(usuario):
    cliente = Client()
    cliente.force_login(usuario)
    return cliente


# --- Login e escritório ------------------------------------------------------------------------


def test_login_com_um_escritorio_ja_seleciona(cenario):
    cliente = Client()
    resposta = cliente.post(reverse("web:login"), {"username": cenario["dono"].email, "password": SENHA})
    assert resposta.status_code == 302 and resposta["Location"] == reverse("web:inicio")
    assert cliente.session["escritorio_id"] == str(cenario["escritorio"].pk)
    assert "Contábil Exemplo" in cliente.get(reverse("web:inicio")).content.decode()


def test_login_errado_e_limite_de_tentativas(cenario, settings):
    settings.WEB_LOGIN_TENTATIVAS = 2
    cliente = Client()
    for _ in range(2):
        resposta = cliente.post(reverse("web:login"), {"username": cenario["dono"].email, "password": "errada"})
        assert "E-mail ou senha incorretos" in resposta.content.decode()
    resposta = cliente.post(reverse("web:login"), {"username": cenario["dono"].email, "password": SENHA})
    assert resposta.status_code == 429


def test_telas_exigem_login(cenario):
    resposta = Client().get(reverse("web:empresas"))
    assert resposta.status_code == 302 and resposta["Location"].startswith(reverse("web:login"))


def test_superuser_sem_escritorio_cria_o_primeiro_e_api_passa_a_funcionar(db, motores_falsos):
    """O caso do 403 "Selecione um escritório" em /api/motores/versoes/ pelo navegador."""
    admin = Usuario.objects.create_superuser(email="admin@exemplo.com.br", password=SENHA, nome="Admin")
    cliente = navegador_de(admin)
    assert cliente.get("/api/motores/versoes/").status_code == 403

    resposta = cliente.get(reverse("web:inicio"))
    assert resposta.status_code == 302 and resposta["Location"] == reverse("web:escritorios")
    pagina = cliente.get(reverse("web:escritorios")).content.decode()
    assert "Criar escritório" in pagina

    resposta = cliente.post(
        reverse("web:escritorio-novo"), {"razao_social": "Escritório do Admin", "documento": "11.222.333/0001-81"}
    )
    assert resposta.status_code == 302
    membership = Membership.objects.get(usuario=admin)
    assert membership.papel == Papel.DONO
    assert cliente.session["escritorio_id"] == str(membership.escritorio_id)
    assert cliente.get("/api/motores/versoes/").status_code == 200
    assert cliente.get(reverse("web:motores")).status_code == 200


def test_quem_ja_tem_escritorio_nao_cria_outro_pela_tela(cenario):
    cliente = navegador_de(cenario["dono"])
    resposta = cliente.post(reverse("web:escritorio-novo"), {"razao_social": "Outro", "documento": "11222333000181"})
    assert resposta.status_code == 404
    assert Escritorio.objects.count() == 1


def test_trocar_de_escritorio_pela_sessao(cenario, criar_escritorio):
    segundo, _, _ = criar_escritorio("Segundo Escritório", dono=cenario["dono"])
    cliente = navegador_de(cenario["dono"])
    # Com dois escritórios e nada na sessão, as telas pedem a escolha.
    assert cliente.get(reverse("web:empresas"))["Location"] == reverse("web:escritorios")
    cliente.post(reverse("web:escritorios"), {"escritorio": str(segundo.pk)})
    assert cliente.session["escritorio_id"] == str(segundo.pk)
    assert "Mercado Bom Preço" not in cliente.get(reverse("web:empresas")).content.decode()
    # A API usa o mesmo escritório da sessão, sem cabeçalho.
    assert cliente.get("/api/escritorio/").json()["razao_social"] == "Segundo Escritório"


def test_escolher_escritorio_alheio_responde_404(cenario, criar_escritorio):
    alheio, _, _ = criar_escritorio("Alheio")
    cliente = navegador_de(cenario["dono"])
    assert cliente.post(reverse("web:escritorios"), {"escritorio": str(alheio.pk)}).status_code == 404


def test_comando_criar_escritorio(db):
    usuario = Usuario.objects.create_superuser(email="root@exemplo.com.br", password=SENHA, nome="Root")
    saida = io.StringIO()
    call_command("criar_escritorio", email="ROOT@exemplo.com.br", stdout=saida)
    assert "é o dono" in saida.getvalue()
    assert Membership.objects.get(usuario=usuario).papel == Papel.DONO


def test_cadastro_pela_tela(db):
    cliente = Client()
    resposta = cliente.post(
        reverse("web:cadastro"),
        {
            "nome": "Ana",
            "email": "ana@exemplo.com.br",
            "senha": "SenhaForte123",
            "escritorio_razao_social": "Ana Contabilidade",
            "escritorio_documento": "11222333000181",
        },
    )
    assert resposta.status_code == 302
    assert Membership.objects.get(usuario__email="ana@exemplo.com.br").papel == Papel.DONO


# --- Páginas abrem ------------------------------------------------------------------------------


def test_todas_as_paginas_abrem(cenario, criar_item, criar_aliquotas_sn, criar_simulacao, motores_falsos):
    item = criar_item(cenario["empresa"], validacao={"erros": [{"origem": "motor", "campo": "ncm", "mensagem": "x"}]})
    cadastro = criar_aliquotas_sn(cenario["simples"])
    simulacao = criar_simulacao(cenario["empresa"])
    cliente = navegador_de(cenario["dono"])
    paginas = [
        reverse("web:inicio"),
        reverse("web:escritorios"),
        reverse("web:motores"),
        reverse("web:empresas"),
        reverse("web:empresa-nova"),
        reverse("web:empresa", args=[cenario["empresa"].pk]),
        reverse("web:empresa", args=[cenario["simples"].pk]),
        reverse("web:empresa-editar", args=[cenario["empresa"].pk]),
        reverse("web:catalogo", args=[cenario["empresa"].pk]),
        reverse("web:catalogo", args=[cenario["empresa"].pk]) + "?status=fila",
        reverse("web:item-novo", args=[cenario["empresa"].pk]),
        reverse("web:item-editar", args=[item.pk]),
        reverse("web:catalogo-importar", args=[cenario["empresa"].pk]),
        reverse("web:aliquotas", args=[cenario["simples"].pk]),
        reverse("web:aliquotas-nova", args=[cenario["simples"].pk]),
        reverse("web:aliquotas-editar", args=[cadastro.pk]),
        reverse("web:simular", args=[cenario["empresa"].pk]),
        reverse("web:simular", args=[cenario["simples"].pk]),
        reverse("web:simular", args=[cenario["empresa"].pk]) + f"?a_partir_de={simulacao.pk}",
        reverse("web:historico"),
        reverse("web:simulacao", args=[simulacao.pk]),
    ]
    for url in paginas:
        resposta = cliente.get(url)
        assert resposta.status_code == 200, (url, resposta.status_code)
    assert cliente.get(reverse("web:catalogo-modelo"))["Content-Type"].startswith("text/csv")


def test_papel_leitura_nao_ve_botoes_nem_abre_formularios(cenario, adicionar_membro):
    leitura = adicionar_membro(cenario["escritorio"], Papel.LEITURA)
    cliente = navegador_de(leitura.usuario)
    assert cliente.get(reverse("web:simular", args=[cenario["empresa"].pk])).status_code == 403
    assert cliente.get(reverse("web:empresa-nova")).status_code == 403
    assert cliente.get(reverse("web:catalogo-importar", args=[cenario["empresa"].pk])).status_code == 403
    pagina = cliente.get(reverse("web:empresa", args=[cenario["empresa"].pk])).content.decode()
    assert "Nova simulação" not in pagina


# --- Empresas e catálogo pela tela -----------------------------------------------------------------


def test_cadastrar_empresa_pela_tela_usa_as_regras_da_api(cenario):
    cliente = navegador_de(cenario["dono"])
    dados = {
        "cnpj": "12.ABC.345/01DE-35",
        "razao_social": "Nova Ltda",
        "regime": "regular",
        "crt": "1",
        "cnae_principal": "4711-3/02",
        "uf": "SP",
        "municipio_ibge": "3550308",
        "ativa": "on",
    }
    resposta = cliente.post(reverse("web:empresa-nova"), dados)
    assert resposta.status_code == 200
    assert "não combina com o regime" in resposta.content.decode()  # CRT 1 com regime regular

    resposta = cliente.post(reverse("web:empresa-nova"), {**dados, "crt": "3"})
    assert resposta.status_code == 302
    assert Empresa.objects.filter(razao_social="Nova Ltda", cnpj="12ABC34501DE35").exists()


def test_importar_e_aprovar_pela_tela(cenario, motores_falsos):
    from catalogo.tests.test_catalogo import CSV, motor_que_recusa

    motores_falsos.rotas[("rr", "POST", MOTOR_RR)] = motor_que_recusa()
    cliente = navegador_de(cenario["dono"])
    arquivo = io.BytesIO(CSV.encode())
    arquivo.name = "itens.csv"
    url = reverse("web:catalogo-importar", args=[cenario["empresa"].pk])
    pagina = cliente.post(url, {"arquivo": arquivo}).content.decode()
    assert "4 criado(s)" in pagina and "2 para revisar" in pagina

    fila = cliente.get(reverse("web:catalogo", args=[cenario["empresa"].pk]) + "?status=fila").content.decode()
    assert "REG-011" in fila
    pendentes = ItemCatalogo.objects.filter(status_classificacao="pendente")
    cliente.post(
        reverse("web:catalogo-aprovar", args=[cenario["empresa"].pk]), {"itens": [str(i.pk) for i in pendentes]}
    )
    assert ItemCatalogo.objects.filter(status_classificacao="aprovado").count() == 2


# --- Simulação pelo formulário -------------------------------------------------------------------


def _form_regular(**extra):
    dados = {
        "rotulo": "Cenário base",
        "data": "2026-06-10",
        "hora": "10:00",
        "itens-TOTAL_FORMS": "2",
        "itens-INITIAL_FORMS": "0",
        "itens-MIN_NUM_FORMS": "0",
        "itens-MAX_NUM_FORMS": "100",
        "itens-0-ncm": "22030000",
        "itens-0-cst": "000",
        "itens-0-c_class_trib": "000001",
        "itens-0-base_calculo": "1.000,00",
        # Linha 1 em branco: ignorada.
    }
    dados.update(extra)
    return dados


def test_simulacao_regular_pelo_formulario_mostra_memoria(cenario, motores_falsos):
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_2026.json")
    cliente = navegador_de(cenario["dono"])
    resposta = cliente.post(reverse("web:simular", args=[cenario["empresa"].pk]), _form_regular())
    assert resposta.status_code == 302, erros_na_pagina(resposta)
    simulacao = Simulacao.objects.get()
    assert simulacao.rotulo == "Cenário base" and simulacao.itens.count() == 1
    enviado = motores_falsos.json_da_ultima("rr")
    assert enviado["itens"][0]["baseCalculo"] == "1000.00"
    assert enviado["dhFatoGerador"].startswith("2026-06-10T10:00:00")

    pagina = cliente.get(resposta["Location"]).content.decode()
    assert "Memória de cálculo do motor" in pagina
    assert "A base de cálculo utilizada é de R$ 1.000,00" in pagina
    assert "alíquota de teste do motor (2026)" in pagina
    assert "regime_regular 1.7.2" in pagina


def test_erro_de_validacao_volta_para_o_campo_do_item(cenario, motores_falsos):
    cliente = navegador_de(cenario["dono"])
    resposta = cliente.post(reverse("web:simular", args=[cenario["empresa"].pk]), _form_regular(**{"itens-0-cst": "0"}))
    assert resposta.status_code == 200
    assert re.search(r'name="itens-0-cst"[^>]*is-invalid|is-invalid[^>]*name="itens-0-cst"', resposta.content.decode())
    assert motores_falsos.ultima("rr") is None


def test_simulacao_2027_edita_aliquota_e_guarda_valor_anterior(cenario, motores_falsos):
    from simulacoes.tests.test_simulacoes import cadastrar_tabela_2027

    cadastrar_tabela_2027()
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_exemplo_oficial_2027.json")
    cliente = navegador_de(cenario["dono"])
    dados = _form_regular(data="2027-03-10", cbs="8,8", ibs_estadual="0,10", ibs_municipal="")
    resposta = cliente.post(reverse("web:simular", args=[cenario["empresa"].pk]), dados)
    assert resposta.status_code == 302, erros_na_pagina(resposta)
    item = Simulacao.objects.get().itens.get()
    assert item.origem_aliquotas["cbs"]["origem"] == "tabela_tributo360"
    assert item.origem_aliquotas["ibs_estadual"] == {
        "origem": "editada_pelo_usuario",
        "valor": "0.1000",
        "valor_anterior": "0.0500",
        "aliquota_nominal_id": item.origem_aliquotas["ibs_estadual"]["aliquota_nominal_id"],
    }
    pagina = cliente.get(resposta["Location"]).content.decode()
    assert "editada pelo usuário" in pagina and "tabela sugeria 0,05%" in pagina


def test_simples_sem_periodo_orienta_o_cadastro_sem_chamar_o_motor(cenario, motores_falsos):
    cliente = navegador_de(cenario["dono"])
    dados = {
        "data": "2027-03-15",
        "tp_doc": "55",
        "itens-TOTAL_FORMS": "1",
        "itens-INITIAL_FORMS": "0",
        "itens-0-c_ativ_sn": "01",
        "itens-0-c_class_trib": "000001",
        "itens-0-base_calculo": "1000",
    }
    url = reverse("web:simular", args=[cenario["simples"].pk])
    pagina = cliente.post(url, dados).content.decode()
    assert "Não há alíquotas efetivas do Simples para 03/2027" in pagina
    assert reverse("web:aliquotas-nova", args=[cenario["simples"].pk]) + "?periodo=2027-03" in pagina
    assert motores_falsos.ultima("sn") is None

    # Cadastra o período pela tela e a mesma simulação passa.
    cadastro = {"periodo": "2027-03", "fator_r": "on", "I_cbs": "1,1612", "I_ibs": "0,0129"}
    assert cliente.post(reverse("web:aliquotas-nova", args=[cenario["simples"].pk]), cadastro).status_code == 302
    assert AliquotaEfetivaSN.objects.get().anexos == {"I": {"cbs": "1.1612", "ibs": "0.0129"}}
    motores_falsos.gravada("sn", MOTOR_SN, "simples_nfe_crt1.json")
    resposta = cliente.post(url, dados)
    assert resposta.status_code == 302
    pagina = cliente.get(resposta["Location"]).content.decode()
    assert "cadastro do período" in pagina and "PGDAS-D 03/2027" in pagina


def test_motor_fora_do_ar_mostra_mensagem(cenario, motores_falsos):
    motores_falsos.fora_do_ar.add("rr")
    cliente = navegador_de(cenario["dono"])
    pagina = cliente.post(reverse("web:simular", args=[cenario["empresa"].pk]), _form_regular()).content.decode()
    assert "Motor de cálculo indisponível" in pagina
    assert not Simulacao.objects.exists()


# --- Histórico e comparação ---------------------------------------------------------------------


def test_comparar_mostra_premissas_e_versoes(cenario, criar_simulacao):
    outra_versao = VersaoMotor.objects.create(motor="regime_regular", versao_app="1.8.0", versao_db="V0080")
    a = criar_simulacao(cenario["empresa"], rotulo="Base")
    b = criar_simulacao(cenario["empresa"], rotulo="Alternativo", versao_motor=outra_versao, total_cbs="19.00")
    cliente = navegador_de(cenario["dono"])
    resposta = cliente.get(reverse("web:comparar") + f"?ids={a.pk}&ids={b.pk}")
    pagina = resposta.content.decode()
    assert resposta.status_code == 200
    assert "Base" in pagina and "Alternativo" in pagina
    assert "1.7.2" in pagina and "1.8.0" in pagina
    assert "versões diferentes do motor" in pagina
    assert "+10,00" in pagina  # diferença de tributos para a referência


def test_comparar_exige_de_2_a_4(cenario, criar_simulacao):
    a = criar_simulacao(cenario["empresa"])
    cliente = navegador_de(cenario["dono"])
    assert cliente.get(reverse("web:comparar") + f"?ids={a.pk}")["Location"] == reverse("web:historico")


def test_duplicar_cenario_preenche_o_formulario(cenario, motores_falsos):
    motores_falsos.gravada("rr", MOTOR_RR, "regime_regular_2026.json")
    cliente = navegador_de(cenario["dono"])
    cliente.post(reverse("web:simular", args=[cenario["empresa"].pk]), _form_regular())
    simulacao = Simulacao.objects.get()
    pagina = cliente.get(
        reverse("web:simular", args=[cenario["empresa"].pk]) + f"?a_partir_de={simulacao.pk}"
    ).content.decode()
    assert 'value="22030000"' in pagina and "Cenário base (variação)" in pagina


# --- Visual (Tailwind + HTMX + Alpine) ---------------------------------------------------------


def test_painel_mostra_contagens_da_carteira(cenario, criar_item, criar_simulacao):
    criar_item(cenario["empresa"], status_classificacao=ItemCatalogo.Status.PENDENTE)
    criar_item(cenario["empresa"], status_classificacao=ItemCatalogo.Status.REVISAR)
    criar_item(cenario["empresa"], status_classificacao=ItemCatalogo.Status.APROVADO)
    criar_simulacao(cenario["empresa"], rotulo="Cenário do mês")
    resposta = navegador_de(cenario["dono"]).get(reverse("web:inicio"))
    assert resposta.status_code == 200
    contexto = resposta.context
    assert (contexto["total_empresas"], contexto["simulacoes_no_mes"]) == (2, 1)
    assert (contexto["itens_pendentes"], contexto["itens_revisar"]) == (1, 1)
    assert "Cenário do mês" in resposta.content.decode()


@pytest.mark.parametrize(
    ("rota", "secao"),
    [
        ("web:inicio", "inicio"),
        ("web:empresas", "empresas"),
        ("web:historico", "simulacoes"),
        ("web:motores", "motores"),
    ],
)
def test_menu_lateral_destaca_a_secao(cenario, rota, secao, motores_falsos):
    resposta = navegador_de(cenario["dono"]).get(reverse(rota))
    assert resposta.context["secao_menu"] == secao


def test_paginas_de_empresa_destacam_empresas_no_menu(cenario):
    resposta = navegador_de(cenario["dono"]).get(reverse("web:catalogo", args=[cenario["empresa"].pk]))
    assert resposta.context["secao_menu"] == "empresas"
    assert 'class="nav-link nav-ativo"' in resposta.content.decode().replace("  ", " ")


def test_filtro_de_empresas_serve_o_htmx_com_a_mesma_view(cenario):
    resposta = navegador_de(cenario["dono"]).get(reverse("web:empresas") + "?q=mercado", HTTP_HX_REQUEST="true")
    pagina = resposta.content.decode()
    assert 'id="resultado"' in pagina and "Mercado Bom Preço" in pagina and "Padaria Simples" not in pagina
