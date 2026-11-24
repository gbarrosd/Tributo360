"""Clientes dos motores da Receita. Use as fábricas abaixo; os testes as substituem."""

from .danfse import DanfseClient
from .regime_regular import RegimeRegularClient
from .simples import SimplesClient


def regime_regular():
    return RegimeRegularClient()


def simples():
    return SimplesClient()


def danfse():
    return DanfseClient()


__all__ = ["DanfseClient", "RegimeRegularClient", "SimplesClient", "danfse", "regime_regular", "simples"]
