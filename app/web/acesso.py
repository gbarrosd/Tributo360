"""Controle de acesso das telas: login, escritório selecionado e papel (mesma matriz da API)."""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from contas.papeis import Acao, pode


def tela(acao=Acao.VER):
    """Decorator das views de tela.

    - sem login: vai para a tela de login;
    - sem escritório selecionado: vai para a escolha do escritório (é aqui que um superuser recém-criado
      cria o primeiro escritório);
    - papel sem permissão: 403.
    """

    def decorador(view):
        @wraps(view)
        def envolvida(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if getattr(request, "membership", None) is None:
                messages.info(request, "Escolha o escritório com que você vai trabalhar.")
                return redirect("web:escritorios")
            if not pode(request.membership.papel, acao):
                raise PermissionDenied("Seu papel neste escritório não permite esta ação.")
            return view(request, *args, **kwargs)

        envolvida.acao = acao
        return envolvida

    return decorador


def permissoes(membership):
    """{"simular": True, ...} para esconder botões que o papel não pode usar."""
    if membership is None:
        return {}
    return {acao.value: pode(membership.papel, acao) for acao in Acao}


def destino_seguro(request, url, padrao):
    """Só redireciona para endereço do próprio site (evita open redirect pelo campo "próximo")."""
    if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return url
    return padrao
