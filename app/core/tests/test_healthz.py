import httpx
import pytest

from engines import versoes


@pytest.fixture
def motores_simulados(monkeypatch, settings):
    settings.MOTORES = {
        "regime_regular": {"url": "http://rr", "versao_path": "/api/calculadora/dados-abertos/versao"},
        "simples": {"url": "http://sn", "versao_path": "/api/versao"},
        "danfse": {"url": "http://df", "versao_path": "/health/ready", "versao_fixa": "1.0.0"},
    }
    fora_do_ar = set()

    def responder(request):
        if request.url.host in fora_do_ar:
            raise httpx.ConnectError("recusado", request=request)
        if request.url.host == "rr":
            return httpx.Response(200, json={"versaoApp": "1.7.2", "versaoDb": "V0073", "dataVersaoDb": "2026-10-30"})
        if request.url.host == "sn":
            return httpx.Response(200, json={"versaoApp": "1.0.2", "versaoDb": "V0073", "extra": "ignorado"})
        return httpx.Response(200, text="ok")

    cliente_original = httpx.Client

    def cliente_falso(**kwargs):
        kwargs.pop("trust_env", None)
        return cliente_original(transport=httpx.MockTransport(responder), **kwargs)

    monkeypatch.setattr(versoes.httpx, "Client", cliente_falso)
    return fora_do_ar


@pytest.mark.django_db
def test_healthz_mostra_versao_de_cada_motor(client, motores_simulados):
    resposta = client.get("/healthz")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert corpo["banco"] == "ok"
    assert corpo["motores"]["regime_regular"] == {
        "status": "ok",
        "versaoApp": "1.7.2",
        "versaoDb": "V0073",
        "dataVersaoDb": "2026-10-30",
    }
    assert corpo["motores"]["simples"] == {"status": "ok", "versaoApp": "1.0.2", "versaoDb": "V0073"}
    assert corpo["motores"]["danfse"] == {"status": "ok", "versaoApp": "1.0.0"}


@pytest.mark.django_db
def test_healthz_degradado_quando_motor_cai(client, motores_simulados):
    motores_simulados.add("sn")
    corpo = client.get("/healthz").json()
    assert corpo["status"] == "degradado"
    assert corpo["motores"]["simples"] == {"status": "indisponivel", "erro": "ConnectError"}
    assert corpo["motores"]["regime_regular"]["status"] == "ok"


@pytest.mark.django_db
def test_motor_que_volta_aparece_sem_esperar_cache(client, motores_simulados):
    motores_simulados.add("rr")
    assert client.get("/healthz").json()["status"] == "degradado"
    motores_simulados.clear()
    assert client.get("/healthz").json()["status"] == "ok"
