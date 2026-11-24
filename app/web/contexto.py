from .acesso import permissoes

SECOES = {
    "inicio": "inicio",
    "historico": "simulacoes",
    "simulacao": "simulacoes",
    "comparar": "simulacoes",
    "motores": "motores",
    "escritorios": "escritorios",
    "escritorio-novo": "escritorios",
}


def secao_do_menu(request):
    """Item do menu lateral a destacar. Tudo que pertence a uma empresa (catálogo, alíquotas, simular) é Empresas."""
    nome = getattr(getattr(request, "resolver_match", None), "url_name", None) or ""
    return SECOES.get(nome, "empresas" if nome else "")


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
        "secao_menu": secao_do_menu(request),
    }
