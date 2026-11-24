from .acesso import permissoes


def sessao(request):
    """Escritório ativo, outros escritórios do usuário e permissões, para o menu de todas as telas."""
    usuario = getattr(request, "user", None)
    if usuario is None or not usuario.is_authenticated:
        return {}
    membership = getattr(request, "membership", None)
    return {
        "membership_ativa": membership,
        "escritorios_do_usuario": [
            m.escritorio
            for m in usuario.memberships.filter(ativo=True)
            .select_related("escritorio")
            .order_by("escritorio__razao_social")
        ],
        "pode": permissoes(membership),
    }
