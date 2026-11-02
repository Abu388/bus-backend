from rest_framework import serializers
from apps.fares.models import Fare


class FareSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fare
        read_only_fields = ['id', 'created_at', 'updated_at', 'configured_by', 'effective_to'] # this all a read only field because it is set by the system and not by the user
        fields = ['id', 'created_at','route' ,
                   'updated_at', 'effective_from', 'effective_to',
                     'amount', 'configured_by',  ] # the wright parts are amount, effective_from, route,
    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Fare amount must be a positive value.")
        return value