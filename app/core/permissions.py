from rest_framework.permissions import BasePermission

from contas.papeis import Acao, pode


class TemEscritorio(BasePermission):
    """Exige usuário autenticado com uma membership ativa resolvida para a requisição."""

    message = "Selecione um escritório ao qual você tenha acesso (cabeçalho X-Escritorio)."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request, "membership", None))


class PapelPermite(BasePermission):
    """Checa a matriz de papéis. A view declara `acoes = {"list": Acao.VER, "create": ..., ...}`.

    Ações sem entrada em `acoes` são negadas (falha fechada).
    """

    message = "Seu papel neste escritório não permite esta ação."

    def has_permission(self, request, view):
        if not hasattr(view, request.method.lower()):
            return True  # método não implementado: deixa o DRF responder 405
        membership = getattr(request, "membership", None)
        if membership is None:
            return False
        nome = getattr(view, "action", None) or request.method.lower()
        acao = getattr(view, "acoes", {}).get(nome)
        if acao is None:
            return False
        return pode(membership.papel, Acao(acao))
