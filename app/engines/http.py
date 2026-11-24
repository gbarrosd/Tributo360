"""Base HTTP comum aos clientes dos motores.

Política (planejamento, seção 3):

- timeout de 5 s, conexão em 2 s;
- uma nova tentativa só em erro de rede ou 5xx (os motores não guardam estado, então repetir é seguro);
- circuit breaker simples: depois de N falhas seguidas, o motor fica "aberto" por alguns segundos e as
  chamadas falham na hora com `MotorIndisponivel`, sem travar a tela esperando timeout;
- log estruturado com motor, caminho, status, duração e o contexto recebido (ex.: `simulacao_id`).

O estado do circuito fica no cache do Django (Redis em produção), então vale para todos os workers.
"""

import logging
import time

import httpx
from django.conf import settings
from django.core.cache import cache

from .contratos import ErroMotor, Motor

logger = logging.getLogger("engines")


class ErroDeMotor(Exception):
    """Base dos erros que os clientes levantam."""

    def __init__(self, motor, mensagem):
        super().__init__(mensagem)
        self.motor = Motor(motor)
        self.mensagem = mensagem


class MotorIndisponivel(ErroDeMotor):
    """Rede, timeout, 5xx ou circuito aberto. A interface mostra "motor indisponível"."""


class OperacaoRecusada(ErroDeMotor):
    """O motor recusou a operação inteira (HTTP 400/422), não um item."""

    def __init__(self, motor, mensagem, erros=(), status=None):
        super().__init__(motor, mensagem)
        self.erros = tuple(erros)
        self.status = status


def erro_de_problem(dado):
    """Converte um erro RFC 7807 dos motores (`code`, `title`, `detail`, `pointer`) em `ErroMotor`."""
    return ErroMotor(
        codigo=str(dado.get("code") or ""),
        titulo=str(dado.get("title") or ""),
        detalhe=str(dado.get("detail") or ""),
        campo=str(dado.get("pointer") or ""),
    )


class Circuito:
    """Circuit breaker por motor, guardado no cache."""

    def __init__(self, motor):
        self.motor = motor
        self._chave_falhas = f"motores:circuito:{motor}:falhas"
        self._chave_aberto = f"motores:circuito:{motor}:aberto"

    @property
    def aberto(self):
        return bool(cache.get(self._chave_aberto))

    def sucesso(self):
        cache.delete_many([self._chave_falhas, self._chave_aberto])

    def falha(self):
        janela = settings.MOTORES_CIRCUITO_JANELA_SEGUNDOS
        cache.add(self._chave_falhas, 0, janela)
        try:
            falhas = cache.incr(self._chave_falhas)
        except ValueError:  # a chave expirou entre o add e o incr
            cache.set(self._chave_falhas, 1, janela)
            falhas = 1
        if falhas >= settings.MOTORES_CIRCUITO_FALHAS:
            cache.set(self._chave_aberto, True, settings.MOTORES_CIRCUITO_ABERTO_SEGUNDOS)
            logger.warning("motor=%s circuito=aberto falhas=%s", self.motor, falhas)


class ClienteMotor:
    """Cliente HTTP de um motor. Subclasses definem `motor` e os métodos de negócio."""

    motor: Motor

    def __init__(self, base_url=None, transport=None, timeout=None):
        cfg = settings.MOTORES[self.motor]
        self.base_url = (base_url or cfg["url"]).rstrip("/")
        self.circuito = Circuito(self.motor)
        self._transport = transport
        self._timeout = timeout or httpx.Timeout(
            settings.MOTORES_TIMEOUT_CALCULO, connect=settings.MOTORES_TIMEOUT_CONEXAO
        )

    def _cliente(self):
        return httpx.Client(timeout=self._timeout, transport=self._transport, trust_env=False)

    def requisitar(self, metodo, caminho, contexto=None, **kwargs):
        """Faz a chamada com nova tentativa e circuito. Devolve `(resposta, duracao_ms)`.

        Respostas 4xx voltam para o cliente decidir o que fazer; só rede/5xx viram `MotorIndisponivel`.
        """
        if self.circuito.aberto:
            raise MotorIndisponivel(self.motor, "Motor temporariamente indisponível. Tente de novo em instantes.")

        url = self.base_url + caminho
        contexto = contexto or {}
        inicio = time.monotonic()
        ultima_falha = None
        with self._cliente() as cliente:
            for tentativa in (1, 2):
                try:
                    resposta = cliente.request(metodo, url, **kwargs)
                except httpx.TransportError as exc:
                    ultima_falha = exc.__class__.__name__
                else:
                    if resposta.status_code < 500:
                        duracao = int((time.monotonic() - inicio) * 1000)
                        self.circuito.sucesso()
                        self._log(logging.INFO, metodo, caminho, resposta.status_code, duracao, tentativa, contexto)
                        return resposta, duracao
                    ultima_falha = f"HTTP {resposta.status_code}"

        duracao = int((time.monotonic() - inicio) * 1000)
        self.circuito.falha()
        self._log(logging.WARNING, metodo, caminho, ultima_falha, duracao, 2, contexto)
        raise MotorIndisponivel(self.motor, f"Motor indisponível ({ultima_falha}).")

    def _log(self, nivel, metodo, caminho, status, duracao, tentativas, contexto):
        extras = " ".join(f"{chave}={valor}" for chave, valor in sorted(contexto.items()))
        logger.log(
            nivel,
            "motor=%s metodo=%s caminho=%s status=%s duracao_ms=%s tentativas=%s %s",
            self.motor,
            metodo,
            caminho,
            status,
            duracao,
            tentativas,
            extras,
            extra={"motor": str(self.motor), "duracao_ms": duracao, **contexto},
        )

    @staticmethod
    def json_ou_vazio(resposta):
        try:
            corpo = resposta.json()
        except ValueError:
            return {}
        return corpo if isinstance(corpo, dict | list) else {}

    def recusa(self, resposta):
        """Monta `OperacaoRecusada` a partir de um 400/422 no formato RFC 7807."""
        corpo = self.json_ou_vazio(resposta)
        if not isinstance(corpo, dict):
            corpo = {}
        erros = [erro_de_problem(e) for e in corpo.get("errors") or [] if isinstance(e, dict)]
        mensagem = (
            corpo.get("detail") or corpo.get("mensagem") or f"O motor recusou a operação (HTTP {resposta.status_code})."
        )
        return OperacaoRecusada(self.motor, str(mensagem), erros, status=resposta.status_code)
