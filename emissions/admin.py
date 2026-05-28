from django.contrib import admin
from .models import *

admin.site.register(Company)
admin.site.register(DataUpload)
admin.site.register(EmissionRecord)
admin.site.register(FailedRow)
admin.site.register(AuditLog)