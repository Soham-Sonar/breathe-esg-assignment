import uuid

from django.db import models


class Company(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    name = models.CharField(
        max_length=255
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):

        return self.name


class DataUpload(models.Model):

    SOURCE_CHOICES = [

        ('SAP','SAP'),

        ('UTILITY','UTILITY'),

        ('TRAVEL','TRAVEL'),

    ]

    STATUS_CHOICES = [

        ('PROCESSING','PROCESSING'),

        ('COMPLETED','COMPLETED'),

        ('FAILED','FAILED'),

        ('PARTIAL_SUCCESS','PARTIAL_SUCCESS'),

    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE
    )

    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES
    )

    file = models.FileField(
        upload_to='uploads/'
    )

    uploaded_by = models.CharField(
        max_length=255
    )

    rows_processed = models.IntegerField(
        default=0
    )

    rows_failed = models.IntegerField(
        default=0
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default='PROCESSING'
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:

        indexes = [

            models.Index(
                fields=['company']
            )
        ]


class EmissionRecord(models.Model):

    REVIEW_STATUS = [

        ('PENDING','PENDING'),

        ('APPROVED','APPROVED'),

        ('REJECTED','REJECTED'),

        ('FLAGGED','FLAGGED'),

    ]

    SCOPE_CHOICES = [

        ('SCOPE_1','SCOPE_1'),

        ('SCOPE_2','SCOPE_2'),

        ('SCOPE_3','SCOPE_3'),

    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE
    )

    upload = models.ForeignKey(
        DataUpload,
        on_delete=models.CASCADE
    )

    source_type = models.CharField(
        max_length=20
    )

    scope = models.CharField(
        max_length=20,
        choices=SCOPE_CHOICES
    )

    category = models.CharField(
        max_length=255
    )

    raw_data = models.JSONField()

    raw_quantity = models.CharField(
        max_length=100,
        null=True,
        blank=True
    )

    raw_unit = models.CharField(
        max_length=50,
        null=True,
        blank=True
    )

    activity_value = models.FloatField()

    activity_unit = models.CharField(
        max_length=50
    )

    normalization_notes = models.JSONField(
        default=list,
        blank=True
    )

    confidence_score = models.FloatField(
        null=True,
        blank=True
    )

    parser_version = models.CharField(
        max_length=100,
        default='v1'
    )

    co2e_kg = models.FloatField()

    period_start = models.DateField()

    period_end = models.DateField(
        null=True,
        blank=True
    )

    review_status = models.CharField(
        max_length=20,
        choices=REVIEW_STATUS,
        default='PENDING'
    )

    reviewer_name = models.CharField(
        max_length=255,
        null=True,
        blank=True
    )

    reviewer_notes = models.TextField(
        null=True,
        blank=True
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True
    )

    is_edited = models.BooleanField(
        default=False
    )

    edited_at = models.DateTimeField(
        null=True,
        blank=True
    )

    source_row_id = models.CharField(
        max_length=255
    )

    is_locked = models.BooleanField(
        default=False
    )

    flag_reason = models.TextField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:

        indexes = [

            models.Index(
                fields=['company']
            ),

            models.Index(
                fields=['upload']
            ),

            models.Index(
                fields=['review_status']
            )

        ]

        constraints = [

            models.UniqueConstraint(

                fields=[

                    'upload',

                    'source_row_id'

                ],

                name='unique_upload_source_row'
            )
        ]


class FailedRow(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    upload = models.ForeignKey(
        DataUpload,
        on_delete=models.CASCADE
    )

    row_number = models.IntegerField()

    error_message = models.TextField()

    raw_content = models.JSONField()

    created_at = models.DateTimeField(
        auto_now_add=True
    )


class AuditLog(models.Model):

    ACTIONS = [

        ('UPLOAD','UPLOAD'),

        ('APPROVE','APPROVE'),

        ('REJECT','REJECT'),

        ('EDIT','EDIT'),

    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    emission_record = models.ForeignKey(
        EmissionRecord,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    action = models.CharField(
        max_length=50,
        choices=ACTIONS
    )

    performed_by = models.CharField(
        max_length=255
    )

    notes = models.TextField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )