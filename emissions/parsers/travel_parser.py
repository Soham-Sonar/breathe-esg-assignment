import pandas as pd

from emissions.models import (

    EmissionRecord,

    FailedRow,

    AuditLog,

)


EMISSION_FACTORS = {
    'Economy': 0.255,
    'Business': 0.765,
    'Ground': 0.089,
}


def parse_travel(upload, company, file_path):

    df = pd.read_csv(file_path)

    processed = 0
    failed = 0

    for index, row in df.iterrows():

        try:

            

            travel_class = str(row['Class']).strip()

            

            distance = row['Distance_km']

            if pd.isna(distance):
                raise Exception("Missing distance")

            distance = float(distance)

            factor = EMISSION_FACTORS.get(travel_class)

            

            if factor is None:
                raise Exception(
                    f"Unknown travel class: {travel_class}"
                )

            co2 = distance * factor

            departure = pd.to_datetime(
                row['Departure_Date']
            ).date()

            return_date = pd.to_datetime(
                row['Return_Date']
            ).date()

            status = 'PENDING'
            flag_reason = None

            if distance > 5000:
                status = 'FLAGGED'
                flag_reason = 'Very high travel distance'

            EmissionRecord.objects.create(
                company=company,
                upload=upload,
                source_type='TRAVEL',
                scope='SCOPE_3',
                category=travel_class,
                raw_data=row.fillna('').to_dict(),
                activity_value=distance,
                activity_unit='km',
                co2e_kg=co2,
                period_start=departure,
                period_end=return_date,
                review_status=status,
                source_row_id=str(row['Trip_ID']),
                flag_reason=flag_reason,
            )

            processed += 1

        except Exception as e:

            

            FailedRow.objects.create(
                upload=upload,
                row_number=index + 1,
                error_message=str(e),
                raw_content=row.fillna('').to_dict(),
            )

            failed += 1

    upload.rows_processed = processed
    upload.rows_failed = failed
    upload.status = 'COMPLETED'
    upload.save()

   