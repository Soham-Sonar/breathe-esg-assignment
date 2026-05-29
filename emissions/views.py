from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import (
    Company,
    DataUpload,
    EmissionRecord,
    FailedRow,
    AuditLog,
)

from .models import AuditLog

from .parsers.sap_parser import parse_sap
from .parsers.utility_parser import parse_utility
from .parsers.travel_parser import parse_travel
from .seriallizers import EmissionRecordSerializer, EmissionRecord,DataUploadSerializer, FailedRowSerializer, CompanySerializer,AuditLogSerializer
from rest_framework.views import APIView

from rest_framework.response import Response

from rest_framework import status

from rest_framework.parsers import (

    MultiPartParser,

    FormParser

)
class UploadAPIView(APIView):

    parser_classes = [

        MultiPartParser,

        FormParser

    ]



    def post(self, request):

        file = request.FILES.get('file')
        

        source_type = request.data.get('source_type').strip().upper()

        

        company_id = request.data.get('company_id')

        uploaded_by = request.data.get('uploaded_by', 'Admin')

        try:

            company = Company.objects.get(id=company_id)

        except Company.DoesNotExist:
            return Response(
                {'error': 'Company not found'},
                status=404
            )
        
        upload = DataUpload.objects.create(
            company=company,
            source_type=source_type,
            file=file,
            uploaded_by=uploaded_by,
        )

        upload.save()

        

        if source_type == 'SAP':
            parse_sap(upload, company, upload.file.path)

        elif source_type == 'UTILITY':
            parse_utility(upload, company, upload.file.path)
        elif source_type == 'TRAVEL':
           parse_travel(upload, company, upload.file.path)
        
      

        return Response({
            'message': 'Upload successful',
            'processed': upload.rows_processed,
            'failed': upload.rows_failed,
        })

from .models import EmissionRecord
# from .serializers import EmissionRecordSerializer

from django.db.models import Sum


class DashboardAPIView(APIView):

    def get(self, request):

        company_id = request.GET.get('company_id')

        records = EmissionRecord.objects.filter(
            company_id=company_id
        )

        total_records = records.count()

        pending = records.filter(
            review_status='PENDING'
        ).count()

        approved = records.filter(
            review_status='APPROVED'
        ).count()

        rejected = records.filter(
            review_status='REJECTED'
        ).count()

        flagged = records.filter(
            review_status='FLAGGED'
        ).count()

        scope1 = records.filter(
            scope='SCOPE_1'
        ).aggregate(
            total=Sum('co2e_kg')
        )['total'] or 0

        scope2 = records.filter(
            scope='SCOPE_2'
        ).aggregate(
            total=Sum('co2e_kg')
        )['total'] or 0

        scope3 = records.filter(
            scope='SCOPE_3'
        ).aggregate(
            total=Sum('co2e_kg')
        )['total'] or 0

        serializer = EmissionRecordSerializer(
            records,
            many=True
        )

        return Response({

            'summary': {
                'total_records': total_records,
                'pending': pending,
                'approved': approved,
                'rejected': rejected,
                'flagged': flagged,

                'scope1_total_kg': round(scope1, 2),
                'scope2_total_kg': round(scope2, 2),
                'scope3_total_kg': round(scope3, 2),
            },

            'records': serializer.data
        })
    
from django.utils import timezone
from .models import AuditLog


class ReviewAPIView(APIView):

    def post(self, request, record_id):

        action = request.data.get('action')

        reviewer_name = request.data.get(
            'reviewer_name',
            'Reviewer'
        )

        notes = request.data.get('notes', '')

        try:

            record = EmissionRecord.objects.get(
                id=record_id
            )

        except EmissionRecord.DoesNotExist:

            return Response(
                {'error': 'Record not found'},
                status=404
            )

        if record.is_locked:

            return Response(
                {'error': 'Record already locked'},
                status=400
            )

        if action == 'APPROVE':

            record.review_status = 'APPROVED'

            record.is_locked = True

        elif action == 'REJECT':

            record.review_status = 'REJECTED'

        else:

            return Response(
                {'error': 'Invalid action'},
                status=400
            )

        record.reviewer_name = reviewer_name
        record.reviewer_notes = notes
        record.reviewed_at = timezone.now()

        record.save()

        AuditLog.objects.create(
            emission_record=record,
            action=action,
            performed_by=reviewer_name,
            notes=notes,
        )

        return Response({
            'message': f'Record {action.lower()}ed successfully'
        })
    



class UploadHistoryAPIView(APIView):

    def get(self, request):

        company_id = request.GET.get('company_id')

        uploads = DataUpload.objects.filter(
            company_id=company_id
        ).order_by('-created_at')

        serializer = DataUploadSerializer(
            uploads,
            many=True
        )

        return Response(serializer.data)
    

class FailedRowsAPIView(APIView):

    def get(self, request):

        upload_id = request.GET.get('upload_id')
        company_id = request.GET.get('company_id')

        if upload_id:
            failed_rows = FailedRow.objects.filter(
                upload_id=upload_id
            )

        elif company_id:
            failed_rows = FailedRow.objects.filter(
                upload__company_id=company_id
            )

        else:
            return Response(
                {'error': 'upload_id or company_id required'},
                status=400
            )

        serializer = FailedRowSerializer(
            failed_rows,
            many=True
        )

        return Response(serializer.data)



class CompanyAPIView(APIView):

    def get(self, request):

        companies = Company.objects.all()

        serializer = CompanySerializer(
            companies,
            many=True
        )

        return Response(serializer.data)

    def post(self, request):

        serializer = CompanySerializer(
            data=request.data
        )

        if serializer.is_valid():

            serializer.save()

            return Response(
                serializer.data,
                status=201
            )

        return Response(
            serializer.errors,
            status=400
        )
    

class BulkReviewAPIView(APIView):

    def post(self, request):

        ids = request.data.get('record_ids', [])

        action = request.data.get('action')

        reviewer_name = request.data.get(
            'reviewer_name',
            'Reviewer'
        )

        notes = request.data.get('notes', '')

        records = EmissionRecord.objects.filter(
            id__in=ids
        )

        updated = 0

        for record in records:

            if record.is_locked:
                continue

            if action == 'APPROVE':

                record.review_status = 'APPROVED'

                record.is_locked = True

            elif action == 'REJECT':

                record.review_status = 'REJECTED'

            else:
                continue

            record.reviewer_name = reviewer_name
            record.reviewer_notes = notes
            record.reviewed_at = timezone.now()

            record.save()

            AuditLog.objects.create(
                emission_record=record,
                action=action,
                performed_by=reviewer_name,
                notes=notes,
            )

            updated += 1

        return Response({
            'message': f'{updated} records updated'
        })
    



class AuditLogAPIView(APIView):
    def get(self, request):
        company_id = request.GET.get('company_id')
        logs = AuditLog.objects.filter(
            emission_record__company_id=company_id
        ).order_by('-created_at')[:200]
        serializer = AuditLogSerializer(logs, many=True)
        return Response(serializer.data)
    
    