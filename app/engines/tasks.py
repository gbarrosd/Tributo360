from celery import shared_task
from celery.signals import worker_ready

from .registro import registrar_versoes_atuais
from .sincronizacao import sincronizar_tabelas


@shared_task(name="engines.registrar_versoes")
def registrar_versoes():
    return {str(motor): str(versao) if versao else None for motor, versao in registrar_versoes_atuais().items()}


@shared_task(name="engines.sincronizar_tabelas", autoretry_for=(Exception,), retry_backoff=60, max_retries=3)
def sincronizar_tabelas_task(forcar=False):
    execucao = sincronizar_tabelas(forcar=forcar)
    return None if execucao is None else execucao.contagens


@worker_ready.connect
def _na_subida(sender=None, **kwargs):
    """Na subida do worker: registra as versões e confere se as tabelas estão em dia."""
    registrar_versoes.delay()
    sincronizar_tabelas_task.delay()
