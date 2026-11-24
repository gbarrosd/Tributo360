"""Cópia local das tabelas oficiais (backlog, item 7).

Roda pelo Celery beat. Só trabalha quando a `versao_db` do motor do regime regular muda (ou com
`forcar=True`). Nunca apaga: linha com conteúdo novo vira registro novo, e o que sumiu da tabela
oficial só perde a marca de `vigente`.
"""

import logging

from django.db import transaction
from django.utils import timezone

from . import clientes
from .models import ItemTabela, SincronizacaoTabela, VersaoMotor

logger = logging.getLogger("engines")


def _cst(linha):
    dados = {chave: valor for chave, valor in linha.items() if chave != "classificacoesTributarias"}
    dados["cClassTrib"] = [c.get("codigo") for c in linha.get("classificacoesTributarias") or []]
    return str(linha.get("codigo")), str(linha.get("descricao") or ""), dados


def _simples(campo_codigo):
    def extrair(linha):
        return str(linha.get(campo_codigo)), str(linha.get("descricao") or ""), linha

    return extrair


# tabela → (método do cliente, extrator de código/descrição/dados)
TABELAS = {
    ItemTabela.Tabela.CST: ("situacoes_tributarias", _cst),
    ItemTabela.Tabela.CCLASSTRIB: ("classificacoes_tributarias", _simples("codigo")),
    ItemTabela.Tabela.NCM: ("ncm_vigentes", _simples("ncm")),
    ItemTabela.Tabela.NBS: ("nbs", _simples("codigo")),
}


def _aplicar(tabela, linhas, extrair, versao_db):
    """Grava as linhas de uma tabela. Devolve contagens {novos, alterados, iguais, removidos}."""
    atuais = {}
    for item in ItemTabela.objects.filter(tabela=tabela, vigente=True):
        atuais[item.codigo] = item

    contagem = {"novos": 0, "alterados": 0, "iguais": 0, "removidos": 0}
    vistos, criar, iguais = set(), [], []
    for linha in linhas:
        codigo, descricao, dados = extrair(linha)
        if codigo in vistos:
            continue
        vistos.add(codigo)
        hash_dados = ItemTabela.calcular_hash(dados)
        atual = atuais.get(codigo)
        if atual and atual.hash_dados == hash_dados:
            iguais.append(atual.pk)
            contagem["iguais"] += 1
            continue
        contagem["alterados" if atual else "novos"] += 1
        criar.append(
            ItemTabela(
                tabela=tabela,
                codigo=codigo,
                descricao=descricao,
                dados=dados,
                hash_dados=hash_dados,
                versao_db_origem=versao_db,
                versao_db_ultima=versao_db,
            )
        )

    iguais_set = set(iguais)
    alterados_ou_sumidos = [item.pk for item in atuais.values() if item.pk not in iguais_set]
    contagem["removidos"] = sum(1 for codigo in atuais if codigo not in vistos)
    ItemTabela.objects.filter(pk__in=alterados_ou_sumidos).update(vigente=False)
    ItemTabela.objects.filter(pk__in=iguais).update(versao_db_ultima=versao_db)
    # Conteúdo que já existiu (voltou ao que era) reaproveita o registro antigo; o resto entra em lote.
    antigos = {
        (codigo, hash_dados): pk
        for pk, codigo, hash_dados in ItemTabela.objects.filter(tabela=tabela, vigente=False).values_list(
            "pk", "codigo", "hash_dados"
        )
    }
    reaproveitados = [antigos[(n.codigo, n.hash_dados)] for n in criar if (n.codigo, n.hash_dados) in antigos]
    ItemTabela.objects.filter(pk__in=reaproveitados).update(vigente=True, versao_db_ultima=versao_db)
    ItemTabela.objects.bulk_create([n for n in criar if (n.codigo, n.hash_dados) not in antigos], batch_size=1000)
    return contagem


def sincronizar_tabelas(forcar=False, data_referencia=None, cliente=None):
    """Sincroniza CST, cClassTrib, NCM e NBS. Devolve a `SincronizacaoTabela` (ou `None` se pulou)."""
    cliente = cliente or clientes.regime_regular()
    data_referencia = data_referencia or timezone.localdate()
    versao = VersaoMotor.registrar(cliente.versao())

    ja_feita = SincronizacaoTabela.objects.filter(
        versao_motor__versao_db=versao.versao_db, status=SincronizacaoTabela.Status.CONCLUIDA
    ).exists()
    if ja_feita and not forcar:
        logger.info("sincronizacao=pulada versao_db=%s motivo=ja_sincronizada", versao.versao_db)
        return None

    execucao = SincronizacaoTabela.objects.create(versao_motor=versao, data_referencia=data_referencia)
    try:
        # Baixa tudo antes de gravar: uma tabela que falha não deixa as outras pela metade.
        baixadas = {tabela: getattr(cliente, metodo)(data_referencia) for tabela, (metodo, _) in TABELAS.items()}
        contagens = {}
        with transaction.atomic():
            for tabela, linhas in baixadas.items():
                if not linhas:
                    raise ValueError(f"Tabela {tabela} veio vazia do motor; nada foi alterado.")
                contagens[str(tabela)] = _aplicar(tabela, linhas, TABELAS[tabela][1], versao.versao_db)
    except Exception as exc:
        execucao.status = SincronizacaoTabela.Status.FALHOU
        execucao.erro = f"{exc.__class__.__name__}: {exc}"
        execucao.concluida_em = timezone.now()
        execucao.save()
        logger.exception("sincronizacao=falhou versao_db=%s", versao.versao_db)
        raise

    execucao.status = SincronizacaoTabela.Status.CONCLUIDA
    execucao.contagens = contagens
    execucao.concluida_em = timezone.now()
    execucao.save()
    logger.info("sincronizacao=concluida versao_db=%s contagens=%s", versao.versao_db, contagens)
    return execucao


def vigentes(tabela, codigo=None):
    qs = ItemTabela.objects.filter(tabela=tabela, vigente=True)
    return qs.filter(codigo=codigo) if codigo else qs


__all__ = ["sincronizar_tabelas", "vigentes"]
