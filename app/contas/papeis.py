"""Papéis e matriz de permissões (planejamento, seção 2.3)."""

from enum import StrEnum

from django.db import models


class Papel(models.TextChoices):
    DONO = "dono", "Dono"
    ADMIN = "admin", "Administrador"
    CONTADOR = "contador", "Contador"
    ASSISTENTE = "assistente", "Assistente"
    LEITURA = "leitura", "Leitura"


class Acao(StrEnum):
    GERIR_PLANO = "gerir_plano"
    GERIR_USUARIOS = "gerir_usuarios"
    EDITAR_EMPRESAS = "editar_empresas"
    APROVAR_CLASSIFICACAO = "aprovar_classificacao"
    CADASTRAR_ITENS = "cadastrar_itens"
    SIMULAR = "simular"
    INFORMAR_ALIQUOTAS_SN = "informar_aliquotas_sn"
    RESOLVER_DIVERGENCIA = "resolver_divergencia"
    VER = "ver"


_TODOS = set(Papel)
_GESTAO = {Papel.DONO, Papel.ADMIN}
_RESPONSAVEIS = {Papel.DONO, Papel.ADMIN, Papel.CONTADOR}
_OPERACAO = _RESPONSAVEIS | {Papel.ASSISTENTE}

MATRIZ = {
    Acao.GERIR_PLANO: {Papel.DONO},
    Acao.GERIR_USUARIOS: _GESTAO,
    Acao.EDITAR_EMPRESAS: _RESPONSAVEIS,
    # Aprovação de classificação fica com quem tem responsabilidade profissional.
    Acao.APROVAR_CLASSIFICACAO: _RESPONSAVEIS,
    Acao.CADASTRAR_ITENS: _OPERACAO,
    Acao.SIMULAR: _OPERACAO,
    Acao.INFORMAR_ALIQUOTAS_SN: _RESPONSAVEIS,
    Acao.RESOLVER_DIVERGENCIA: _RESPONSAVEIS,
    Acao.VER: _TODOS,
}


def pode(papel, acao):
    return papel in MATRIZ[Acao(acao)]
