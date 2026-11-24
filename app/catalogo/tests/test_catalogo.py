"""Catálogo de itens: validação pelo motor, importação por planilha e aprovação por papel."""

import io
import json

import httpx
import pytest
from openpyxl import Workbook

from auditoria.models import Auditoria
from catalogo.models import ItemCatalogo
from contas.papeis import Papel
from engines.models import ItemTabela

MOTOR_RR = "/api/calculadora/observabilidade/regime-geral"
URL = "/api/itens/"
NCM_RECUSADO = "99999999"


def motor_que_recusa(ncm_recusado=NCM_RECUSADO):
    """Motor falso: calcula tudo, menos itens com o NCM recusado (REG-011 no campo ncm, como o real)."""

    def responder(request):
        payload = json.loads(request.content)
        objetos = []
        for posicao, item in enumerate(payload["itens"]):
            if item.get("ncm") == ncm_recusado:
                objetos.append(
                    {
                        "nObj": item["numero"],
                        "estadoItem": "INCONSISTENCIA_ENTRADA",
                        "erros": [
                            {
                                "code": "REG-011",
                                "title": "NCM incompatível",
                                "detail": "O NCM informado não é compatível com o cClassTrib.",
                                "pointer": f"#/itens/{posicao}/ncm",
                            }
                        ],
                    }
                )
            else:
                objetos.append({"nObj": item["numero"], "estadoItem": "CALCULADO", "tribCalc": {}})
        return httpx.Response(
            200,
            json={"objetos": objetos, "total": {}},
            headers={"X-CALC-APP-VERSION": "1.7.2", "X-CALC-DB-VERSION": "V0073"},
        )

    return responder


@pytest.fixture
def cenario(criar_escritorio, criar_empresa, cliente_de, motores_falsos):
    escritorio, _, dono = criar_escritorio()
    empresa = criar_empresa(escritorio)
    motores_falsos.rotas[("rr", "POST", MOTOR_RR)] = motor_que_recusa()
    return {"escritorio": escritorio, "dono": dono, "empresa": empresa, "cliente": cliente_de(dono)}


def corpo(empresa, **extra):
    return {
        "empresa": str(empresa.pk),
        "tipo": "produto",
        "codigo": "CERV-350",
        "descricao": "Cerveja lata 350 ml",
        "ncm": "2203.00.00",
        "cst": "000",
        "c_class_trib": "000001",
        **extra,
    }


def test_cadastro_valida_no_motor_e_fica_pendente(cenario, motores_falsos):
    resposta = cenario["cliente"].post(URL, corpo(cenario["empresa"]), format="json")
    assert resposta.status_code == 201, resposta.content
    dados = resposta.json()
    assert dados["ncm"] == "22030000"  # pontos removidos
    assert dados["status_classificacao"] == "pendente"
    assert dados["validacao"]["erros"] == []
    assert "1.7.2" in dados["validacao"]["motor"]
    enviado = motores_falsos.json_da_ultima("rr")
    assert enviado["itens"][0]["cClassTrib"] == "000001" and enviado["itens"][0]["baseCalculo"] == "100.00"
    assert Auditoria.objects.filter(acao="item_catalogo.criar").exists()


def test_ncm_incompativel_vai_para_revisar_com_erro_do_motor(cenario):
    resposta = cenario["cliente"].post(URL, corpo(cenario["empresa"], ncm=NCM_RECUSADO), format="json")
    assert resposta.status_code == 201
    dados = resposta.json()
    assert dados["status_classificacao"] == "revisar"
    assert dados["validacao"]["erros"] == [
        {
            "origem": "motor",
            "campo": "ncm",
            "mensagem": "O NCM informado não é compatível com o cClassTrib.",
            "codigo": "REG-011",
        }
    ]


def test_formato_errado_nao_vai_ao_motor(cenario, motores_falsos):
    resposta = cenario["cliente"].post(URL, corpo(cenario["empresa"], ncm="2203"), format="json")
    assert resposta.json()["status_classificacao"] == "revisar"
    assert resposta.json()["validacao"]["erros"][0]["origem"] == "formato"
    assert motores_falsos.ultima("rr") is None


def test_cclasstrib_fora_do_cst_pela_tabela_oficial(cenario):
    ItemTabela.objects.create(
        tabela="cst",
        codigo="000",
        dados={"cClassTrib": ["000001"]},
        hash_dados="a",
        versao_db_origem="V1",
        versao_db_ultima="V1",
    )
    ItemTabela.objects.create(
        tabela="cclasstrib", codigo="200003", hash_dados="b", versao_db_origem="V1", versao_db_ultima="V1"
    )
    resposta = cenario["cliente"].post(URL, corpo(cenario["empresa"], c_class_trib="200003"), format="json")
    erros = resposta.json()["validacao"]["erros"]
    assert resposta.json()["status_classificacao"] == "revisar"
    assert erros[0]["origem"] == "tabela" and "não pertence ao CST 000" in erros[0]["mensagem"]


def test_motor_fora_do_ar_deixa_pendente_com_aviso(cenario, motores_falsos):
    motores_falsos.fora_do_ar.add("rr")
    resposta = cenario["cliente"].post(URL, corpo(cenario["empresa"]), format="json")
    dados = resposta.json()
    assert dados["status_classificacao"] == "pendente"
    assert "Motor indisponível" in dados["validacao"]["avisos"][0]


def test_codigo_repetido_na_empresa(cenario):
    cenario["cliente"].post(URL, corpo(cenario["empresa"]), format="json")
    resposta = cenario["cliente"].post(URL, corpo(cenario["empresa"]), format="json")
    assert resposta.status_code == 400 and "codigo" in resposta.json()


# --- Aprovação ----------------------------------------------------------------------------------


def test_aprovacao_por_papel(cenario, adicionar_membro, cliente_de, criar_item):
    pendente = criar_item(cenario["empresa"])
    revisar = criar_item(cenario["empresa"], status_classificacao="revisar")
    assistente = cliente_de(adicionar_membro(cenario["escritorio"], Papel.ASSISTENTE))
    corpo_aprovar = {"itens": [str(pendente.pk), str(revisar.pk)]}
    assert assistente.post(f"{URL}aprovar/", corpo_aprovar, format="json").status_code == 403

    contador = cliente_de(adicionar_membro(cenario["escritorio"], Papel.CONTADOR))
    resposta = contador.post(f"{URL}aprovar/", corpo_aprovar, format="json")
    assert resposta.status_code == 200
    assert resposta.json()["aprovados"] == [str(pendente.pk)]
    assert [r["id"] for r in resposta.json()["recusados"]] == [str(revisar.pk)]
    pendente.refresh_from_db()
    assert pendente.status_classificacao == "aprovado" and pendente.aprovado_por is not None


def test_mudar_classificacao_de_aprovado_volta_para_pendente(cenario, criar_item):
    item = criar_item(cenario["empresa"], status_classificacao="aprovado")
    cliente = cenario["cliente"]
    assert (
        cliente.patch(f"{URL}{item.pk}/", {"descricao": "Só o nome"}, format="json").json()["status_classificacao"]
        == "aprovado"
    )
    resposta = cliente.patch(f"{URL}{item.pk}/", {"c_class_trib": "000002"}, format="json")
    assert resposta.json()["status_classificacao"] == "pendente"
    assert resposta.json()["aprovado_por"] is None


def test_delete_inativa(cenario, criar_item):
    item = criar_item(cenario["empresa"])
    assert cenario["cliente"].delete(f"{URL}{item.pk}/").status_code == 204
    item.refresh_from_db()
    assert item.ativo is False


# --- Importação por planilha ------------------------------------------------------------------

CSV = (
    "tipo;codigo;descricao;ncm;nbs;cst;cclasstrib;cativsn\n"
    "produto;ARROZ;Arroz 5 kg;1006.30.21;;200;200003;\n"
    ";AULA;Mensalidade escolar;;122011200;200;200028;\n"
    "produto;ERRADO;Item com NCM recusado;99999999;;000;000001;\n"
    "produto;CURTO;NCM curto;2203;;000;000001;\n"
    "produto;;Sem código;22030000;;000;000001;\n"
    "produto;ARROZ;Arroz repetido;10063021;;200;200003;\n"
)


def importar(cliente, empresa, conteudo, nome="itens.csv"):
    arquivo = io.BytesIO(conteudo if isinstance(conteudo, bytes) else conteudo.encode())
    arquivo.name = nome
    return cliente.post(f"{URL}importar/", {"empresa": str(empresa.pk), "arquivo": arquivo}, format="multipart")


def test_importacao_csv(cenario):
    resposta = importar(cenario["cliente"], cenario["empresa"], CSV)
    assert resposta.status_code == 201, resposta.content
    resumo = resposta.json()
    assert (resumo["linhas"], resumo["criados"], resumo["revisar"]) == (6, 4, 2)
    assert [i["linha"] for i in resumo["ignoradas"]] == [6, 7]

    itens = {i.codigo: i for i in ItemCatalogo.objects.filter(empresa=cenario["empresa"])}
    assert itens["ARROZ"].ncm == "10063021" and itens["ARROZ"].status_classificacao == "pendente"
    assert itens["AULA"].tipo == "servico"  # sem tipo, NBS = serviço
    assert itens["ERRADO"].status_classificacao == "revisar"
    assert itens["ERRADO"].erros_validacao[0]["codigo"] == "REG-011"
    assert itens["CURTO"].erros_validacao[0]["origem"] == "formato"


def test_reimportar_nao_duplica_e_mudanca_volta_para_pendente(cenario):
    importar(cenario["cliente"], cenario["empresa"], CSV)
    ItemCatalogo.objects.filter(codigo="ARROZ").update(status_classificacao="aprovado")

    resumo = importar(cenario["cliente"], cenario["empresa"], CSV).json()
    assert (resumo["criados"], resumo["atualizados"], resumo["sem_mudanca"]) == (0, 0, 4)
    assert ItemCatalogo.objects.get(codigo="ARROZ").status_classificacao == "aprovado"

    alterado = CSV.replace("200;200003", "200;200004", 1)
    resumo = importar(cenario["cliente"], cenario["empresa"], alterado).json()
    assert resumo["atualizados"] == 1
    assert ItemCatalogo.objects.get(codigo="ARROZ").status_classificacao == "pendente"
    assert ItemCatalogo.objects.filter(empresa=cenario["empresa"]).count() == 4


def test_importacao_xlsx_com_numeros(cenario):
    planilha = Workbook()
    aba = planilha.active
    aba.append(["Código", "Descrição", "NCM", "CST", "cClassTrib"])
    aba.append([123, "Cerveja", 22030000, "000", "000001"])
    saida = io.BytesIO()
    planilha.save(saida)
    resposta = importar(cenario["cliente"], cenario["empresa"], saida.getvalue(), "itens.xlsx")
    assert resposta.status_code == 201, resposta.content
    item = ItemCatalogo.objects.get(empresa=cenario["empresa"])
    assert (item.codigo, item.ncm, item.tipo) == ("123", "22030000", "produto")


def test_planilha_sem_colunas_obrigatorias(cenario):
    resposta = importar(cenario["cliente"], cenario["empresa"], "codigo;ncm\nA;22030000\n")
    assert resposta.status_code == 400
    assert "descricao" in str(resposta.json())


def test_arquivo_que_nao_e_planilha(cenario):
    resposta = importar(cenario["cliente"], cenario["empresa"], b"PK\x03\x04lixo", "itens.xlsx")
    assert resposta.status_code == 400
    assert importar(cenario["cliente"], cenario["empresa"], b"x", "itens.pdf").status_code == 400


def test_leitura_nao_importa(cenario, adicionar_membro, cliente_de):
    leitura = cliente_de(adicionar_membro(cenario["escritorio"], Papel.LEITURA))
    assert importar(leitura, cenario["empresa"], CSV).status_code == 403
    assert leitura.get(URL).status_code == 200
