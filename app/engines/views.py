from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from contas.papeis import Acao
from core.permissions import PapelPermite, TemEscritorio

from . import aliquotas
from .models import SincronizacaoTabela, VersaoMotor
from .serializers import AliquotaNominalSerializer, ConsultaAliquotasSerializer, VersaoMotorSerializer
from .versoes import versoes_dos_motores


class VersoesMotoresView(APIView):
    """Painel de versões: o que está rodando agora e o histórico de versões que já calcularam aqui."""

    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {"get": Acao.VER}

    def get(self, request):
        ultima_sync = SincronizacaoTabela.objects.select_related("versao_motor").first()
        return Response(
            {
                "atuais": versoes_dos_motores(),
                "historico": VersaoMotorSerializer(VersaoMotor.objects.all(), many=True).data,
                "ultima_sincronizacao_tabelas": None
                if ultima_sync is None
                else {
                    "versao_db": ultima_sync.versao_motor.versao_db,
                    "status": ultima_sync.status,
                    "iniciada_em": ultima_sync.iniciada_em,
                    "concluida_em": ultima_sync.concluida_em,
                    "contagens": ultima_sync.contagens,
                },
            }
        )


class AliquotasVigentesView(APIView):
    """Alíquotas nominais que a tabela do Tributo360 propõe para uma data e um município (D2)."""

    permission_classes = [IsAuthenticated, TemEscritorio, PapelPermite]
    acoes = {"get": Acao.VER}

    def get(self, request):
        consulta = ConsultaAliquotasSerializer(data=request.query_params)
        consulta.is_valid(raise_exception=True)
        data = consulta.validated_data["data"]
        if not aliquotas.exige_aliquotas(data):
            return Response(
                {
                    "data": data,
                    "exige_aliquotas": False,
                    "mensagem": "Antes de 2027 o motor usa as alíquotas de teste dele; não informe alíquotas.",
                    "aliquotas": {},
                }
            )
        propostas = aliquotas.propostas_da_tabela(data, consulta.validated_data["municipio"])
        return Response(
            {
                "data": data,
                "exige_aliquotas": True,
                "aliquotas": {
                    campo: None if p.registro is None else AliquotaNominalSerializer(p.registro).data
                    for campo, p in propostas.items()
                },
            }
        )
