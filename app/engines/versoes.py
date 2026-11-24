"""Versão de cada motor para o `/healthz` e o painel de versões.

Leitura leve e tolerante a falhas (timeout curto, sem circuito). Os clientes completos ficam em
`engines/clientes/`; o registro em banco (`VersaoMotor`) fica em `engines/registro.py`.
"""

import httpx
from django.conf import settings
from django.core.cache import cache

CHAVE_CACHE = "motores:versoes"

# Campos devolvidos pelos endpoints de versão que interessam para rastreabilidade.
CAMPOS_VERSAO = ("versaoApp", "versaoDb", "dataVersaoDb", "descricaoVersaoApp", "ambiente")


def _consultar(nome, cfg, cliente):
    url = cfg["url"].rstrip("/") + cfg["versao_path"]
    try:
        resposta = cliente.get(url)
    except httpx.HTTPError as exc:
        return {"status": "indisponivel", "erro": exc.__class__.__name__}
    if resposta.status_code != 200:
        return {"status": "indisponivel", "erro": f"HTTP {resposta.status_code}"}

    resultado = {"status": "ok"}
    if cfg.get("versao_fixa") is not None:
        resultado["versaoApp"] = cfg["versao_fixa"] or None
        return resultado
    try:
        corpo = resposta.json()
    except ValueError:
        return {"status": "indisponivel", "erro": "resposta sem JSON"}
    resultado.update({campo: corpo[campo] for campo in CAMPOS_VERSAO if campo in corpo})
    return resultado


def versoes_dos_motores(usar_cache=True):
    if usar_cache and (em_cache := cache.get(CHAVE_CACHE)) is not None:
        return em_cache
    timeout = httpx.Timeout(settings.MOTORES_TIMEOUT)
    with httpx.Client(timeout=timeout, trust_env=False) as cliente:
        versoes = {nome: _consultar(nome, cfg, cliente) for nome, cfg in settings.MOTORES.items()}
    # Só guarda em cache quando todos responderam: um motor fora do ar deve aparecer logo que voltar.
    if all(v["status"] == "ok" for v in versoes.values()):
        cache.set(CHAVE_CACHE, versoes, settings.MOTORES_VERSAO_CACHE_SEGUNDOS)
    return versoes
