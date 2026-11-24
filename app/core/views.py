from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from engines.versoes import versoes_dos_motores


@require_GET
def healthz(request):
    """Saúde do Django e versão de cada motor.

    200 quando o banco responde (mesmo com motor fora: status "degradado"); 503 sem banco.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        banco = "ok"
    except Exception:  # noqa: BLE001 - qualquer falha de banco vira 503
        banco = "indisponivel"

    motores = versoes_dos_motores()
    if banco != "ok":
        status_geral, http = "indisponivel", 503
    elif all(m["status"] == "ok" for m in motores.values()):
        status_geral, http = "ok", 200
    else:
        status_geral, http = "degradado", 200
    return JsonResponse({"status": status_geral, "banco": banco, "motores": motores}, status=http)
