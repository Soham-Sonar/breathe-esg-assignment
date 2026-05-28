from rest_framework import serializers

from .models import (
    Company,
    DataUpload,
    EmissionRecord,
    FailedRow,
)


class CompanySerializer(serializers.ModelSerializer):

    class Meta:
        model = Company
        fields = '__all__'


class DataUploadSerializer(serializers.ModelSerializer):

    class Meta:
        model = DataUpload
        fields = '__all__'


class EmissionRecordSerializer(serializers.ModelSerializer):

    class Meta:
        model = EmissionRecord
        fields = '__all__'


class FailedRowSerializer(serializers.ModelSerializer):

    class Meta:
        model = FailedRow
        fields = '__all__'