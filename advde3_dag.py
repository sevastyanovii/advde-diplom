import logging
import shutil
from datetime import datetime
import zipfile
import tempfile
from airflow.exceptions import AirflowSkipException
import os
from airflow.decorators import dag, task
from airflow.models import Variable

log = logging.getLogger(__name__)

if not shutil.which("virtualenv"):
    log.warning(
        "The 'advde3_dag' DAG requires virtualenv, please install it."
    )
else:
    @dag(schedule_interval=None, start_date=datetime(2021, 1, 1), catchup=False, tags=['advde'])
    def advde3_taskflow():

        def create_temp_file():
            tmpfile = tempfile.NamedTemporaryFile(prefix='aws')
            tmpfile.close()
            return tmpfile.name

        def unzip_file(zf):
            if zipfile.is_zipfile(zf):
                with zipfile.ZipFile(zf) as zip_file:
                    for member in zip_file.namelist():
                        filename = os.path.basename(member)
                        # skip directories
                        if not filename:
                            continue

                        if member.endswith(".csv") and not member.startswith(".") and member.find("/") < 0:
                            # copy file (taken from zipfile's extract)
                            source = zip_file.open(member)
                            unzipped = create_temp_file()
                            target = open(unzipped, "wb")
                            with source, target:
                                shutil.copyfileobj(source, target)
                            print(f'unzipped {member} into {unzipped}')
                            return unzipped
                        print(f'File "{zf}" is not contain data. Will be skipped...')
                    else:
                        print(f'File "{zf}" is not zipped. Will be skipped...')

        @task
        def init_params():
            val = {'par1': Variable.get('aws_access_key_id')
                , 'par2': Variable.get('aws_secret_access_key')
                , 'par3': Variable.get('clickhouse_secret_url')}
            return val

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['boto3'],
        )
        def receive_message(params0):
            import boto3
            import json
            import logging

            print(f'Accepted params: {params0}')
            session = boto3.Session(
                params0['par1']
                , params0['par2']
            )
            sqs_client = session.client('sqs', region_name="eu-central-1")
            queue_url = "https://sqs.eu-central-1.amazonaws.com/622634767039/AdvdeQueue"
            response = sqs_client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=3,
            )
            print(f"Original response: {response}")
            print(f"Number of messages received: {len(response.get('Messages', []))}")

            if len(response.get('Messages', [])) == 1:
                sqs_message = response.get("Messages", [])[0]
                message_body = sqs_message["Body"]
                handle = sqs_message['ReceiptHandle']
                print(f"Message body: {json.loads(message_body)}")
                print(f"Receipt Handle: {handle}")

                sqs_client.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=handle
                )
                return message_body
            else:
                return ""

        @task(multiple_outputs=True)
        def get_file_name(message):
            import ast
            if len(message) == 0:
                raise AirflowSkipException("task skipped: 'get_file_name'")

            print(f"accepted message: {message}")
            d = ast.literal_eval(message)
            return {"bucket": d["Records"][0]["s3"]["bucket"]["arn"], "file": d["Records"][0]["s3"]["object"]["key"]}

        @task
        def download_file3(file):
            import boto3
            s3 = boto3.resource(service_name='s3',
                                aws_access_key_id=Variable.get('aws_access_key_id')
                                , aws_secret_access_key=Variable.get('aws_secret_access_key')
                                )
            tmpfile = tempfile.NamedTemporaryFile(prefix='aws')
            tmpfile.close()
            s3.Bucket("advde-backet2").download_file(Key=file, Filename=tmpfile.name)
            print(f'file {file} downloaded to {tmpfile.name} "2" OK')
            return tmpfile.name

        @task
        def unzip_file0(file):
            unzipped = unzip_file(file)
            if len(unzipped) == 0:
                raise AirflowSkipException()
            return unzipped

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['clickhouse_driver', 'pandas'],
        )
        def load_data0(file):
            def load_data(csv_file):
                from clickhouse_driver import Client
                import pandas as pd

                client = Client(host='130.61.143.82', settings={'use_numpy': True})
                client.execute('DROP TABLE IF EXISTS advdedb.ride')
                data = pd.read_csv(csv_file)
                if ('ride_id' in data.columns):
                    client.execute(
                        """
                    CREATE TABLE advdedb.ride (
                          ride_id             String
                        , rideable_type       String
                        , started_at          DateTime
                        , ended_at            DateTime
                        , start_station_name  String
                        , start_station_id    String
                        , end_station_name    String
                        , end_station_id      String
                        , start_lat           Float64
                        , start_lng           Float64
                        , end_lat             Float64
                        , end_lng             Float64
                        , member_casual       String 
                     ) ENGINE = MergeTree ORDER BY (started_at)
                     """)

                    inserted = client.insert_dataframe('INSERT INTO advdedb.ride VALUES',
                                                       pd.DataFrame(data)
                                                       )
                    print('Inserted', inserted)
                    return False
                else:
                    return True

            return load_data(file)

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['clickhouse_driver', 'pandas'],
        )
        def report1(skip_other):
            from clickhouse_driver import Client
            import pandas as pd
            import random as rnd
            import tempfile

            print('Accepted', skip_other)

            if (not skip_other):
                def create_temp_file0():
                    tmpfile = tempfile.NamedTemporaryFile(prefix='aws')
                    tmpfile.close()
                    return tmpfile.name

                client = Client(host='130.61.143.82', settings={'use_numpy': True})
                data = client.query_dataframe("""
                select toStartOfDay(started_at) trip_date, count(1) day_count
                  from advdedb.ride
                  group by toStartOfDay(started_at)
                  order by trip_date
                """)
                sfx = pd.to_datetime(data.loc[1]['trip_date']).strftime('%Y%m')
                report_file = f'rep1_{sfx}_{round(rnd.random() * 1000)}.csv'
                rep1file_temp = create_temp_file0()
                data.to_csv(rep1file_temp, index=False)
                print(f'created report1 file "{rep1file_temp}"')
                return {"repfile": report_file, "temp_file": rep1file_temp, "sfx": sfx, "skip_other": False}
            else:
                return {"repfile": None, "temp_file": None, "sfx":  None, "skip_other": True}

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['clickhouse_driver', 'pandas'],
        )
        def report2(skip_other):
            from clickhouse_driver import Client
            import pandas as pd
            import random as rnd
            import tempfile

            print('Accepted', skip_other)

            if (not skip_other):
                def create_temp_file0():
                    tmpfile = tempfile.NamedTemporaryFile(prefix='aws')
                    tmpfile.close()
                    return tmpfile.name

                client = Client(host='130.61.143.82', settings={'use_numpy': True})
                sfx = pd.to_datetime(client.query_dataframe("select started_at from advdedb.ride limit 1").loc[0]['started_at']).strftime('%Y%m')
                data = client.query_dataframe("""
                    select toStartOfDay(started_at) rep_day
                           , round(avg(dateDiff('minute', started_at, ended_at))) time_delta_minute
                      from advdedb.ride
                      group by toStartOfDay(started_at)
                      order by rep_day
                """)
                report_file = f'rep2_{sfx}_{round(rnd.random() * 1000)}.csv'
                repfile_temp = create_temp_file0()
                data.to_csv(repfile_temp, index=False)
                return {"repfile": report_file, "temp_file": repfile_temp, "sfx": sfx, "skip_other": False}
            else:
                return {"repfile": None, "temp_file": None, "sfx": None, "skip_other": True}


        def upload_rep(repdata):
            import boto3

            print(f'Accepted repdata: {repdata}')

            bucket = 'advde-bucket'
            s3 = boto3.resource('s3',
                                aws_access_key_id=Variable.get('aws_access_key_id')
                                , aws_secret_access_key=Variable.get('aws_secret_access_key')
                                )
            print(f'about to upload file {repdata["temp_file"]} to {repdata["repfile"]} on bucket {bucket}')
            s3.Bucket(bucket).upload_file(repdata["temp_file"], repdata["repfile"])
            print(f"Uploaded successfully: {repdata['repfile']} on bucket {bucket}")

        @task
        def upload_rep1(repdata):
            if not repdata['skip_other']:
                upload_rep(repdata)

        @task
        def upload_rep2(repdata):
            if not repdata['skip_other']:
                upload_rep(repdata)

        params = init_params()
        message = receive_message(params)
        file_data = get_file_name(message=message)
        downloaded = download_file3(file_data["file"])
        unzipped = unzip_file0(downloaded)
        load_result_v1 = load_data0(unzipped)
        rep_data1 = report1(load_result_v1)
        upload_rep1(rep_data1)
        rep_data2 = report2(load_result_v1)
        upload_rep2(rep_data2)

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['clickhouse_driver', 'pandas'],
        )
        def load_data0_v2(file):
            def load_data(csv_file):
                from clickhouse_driver import Client
                import pandas as pd

                client = Client(host='130.61.143.82', settings={'use_numpy': True})
                client.execute('DROP TABLE IF EXISTS advdedb.ride_v2')
                data = pd.read_csv(csv_file)
                if ('tripduration' in data.columns):

                    client.execute(
                        """
                        CREATE TABLE advdedb.ride_v2 (
                             "tripduration" Float64
                           , "starttime" DateTime
                           , "stoptime" DateTime
                           , "start station id" Float64
                           , "start station name" String
                           , "start station latitude" Float64
                           , "start station longitude" Float64
                           , "end station id" Float64
                           , "end station name" String
                           , "end station latitude" Float64
                           , "end station longitude" Float64
                           , "bikeid" Float64
                           , "usertype" String
                           , "birth year" Int64
                           , "gender" Int64
                        ) ENGINE = MergeTree ORDER BY ("starttime")
                     """)

                    inserted = client.insert_dataframe('INSERT INTO advdedb.ride_v2 VALUES', pd.DataFrame(data))
                    print('Inserted rows', inserted)
                    return False
                else:
                    return True

            return load_data(file)

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['clickhouse_driver', 'pandas'],
        )
        def report1_v2(skip_other):
            from clickhouse_driver import Client
            import pandas as pd
            import random as rnd
            import tempfile

            print('Accepted', skip_other)

            if not skip_other:
                def create_temp_file0():
                    tmpfile = tempfile.NamedTemporaryFile(prefix='aws')
                    tmpfile.close()
                    return tmpfile.name

                client = Client(host='130.61.143.82', settings={'use_numpy': True})
                data = client.query_dataframe("""
                select toStartOfDay(starttime) trip_date, count(1) day_count
                  from advdedb.ride_v2
                  group by toStartOfDay(starttime)
                  order by trip_date
                """)
                sfx = pd.to_datetime(data.loc[1]['trip_date']).strftime('%Y%m')
                report_file = f'rep1_{sfx}_{round(rnd.random() * 1000)}.csv'
                rep1file_temp = create_temp_file0()
                data.to_csv(rep1file_temp, index=False)
                print(f'created report1 file "{rep1file_temp}"')
                return {"repfile": report_file, "temp_file": rep1file_temp, "sfx": sfx, "skip_other": False}
            else:
                return {"repfile": None, "temp_file": None, "sfx": None, "skip_other": True}

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['clickhouse_driver', 'pandas'],
        )
        def report2_v2(skip_other):
            from clickhouse_driver import Client
            import pandas as pd
            import random as rnd
            import tempfile

            print('Accepted', skip_other)

            if not skip_other:
                def create_temp_file0():
                    tmpfile = tempfile.NamedTemporaryFile(prefix='aws')
                    tmpfile.close()
                    return tmpfile.name

                client = Client(host='130.61.143.82', settings={'use_numpy': True})
                data = client.query_dataframe("""
                    select toStartOfDay(starttime) rep_day
                           , round(avg(dateDiff('minute', starttime, stoptime))) time_delta_minute
                      from advdedb.ride_v2
                      group by toStartOfDay(starttime)
                      order by rep_day
                """)
                sfx = pd.to_datetime(client.query_dataframe("select starttime from advdedb.ride_v2 limit 1").loc[0]['starttime']).strftime('%Y%m')

                report_file = f'rep2_{sfx}_{round(rnd.random() * 1000)}.csv'
                repfile_temp = create_temp_file0()
                data.to_csv(repfile_temp, index=False)
                return {"repfile": report_file, "temp_file": repfile_temp, "sfx": sfx, "skip_other": False}
            else:
                return {"repfile": None, "temp_file": None, "sfx": None, "skip_other": True}

        @task.virtualenv(
            use_dill=True,
            system_site_packages=False,
            requirements=['clickhouse_driver', 'pandas'],
        )
        def report3_v2(skip_other):
            from clickhouse_driver import Client
            import pandas as pd
            import random as rnd
            import tempfile

            print('Accepted', skip_other)

            if not skip_other:
                def create_temp_file0():
                    tmpfile = tempfile.NamedTemporaryFile(prefix='aws')
                    tmpfile.close()
                    return tmpfile.name

                client = Client(host='130.61.143.82', settings={'use_numpy': True})

                sfx = pd.to_datetime(client.query_dataframe("select starttime from advdedb.ride_v2 limit 1").loc[0]['starttime']).strftime('%Y%m')
                data = client.query_dataframe("""
                    select count(1) count_trip, gender from advdedb.ride_v2 group by gender order by gender
                """)
                report_file = f'rep3_{sfx}_{round(rnd.random() * 1000)}.csv'
                repfile_temp = create_temp_file0()
                data.to_csv(repfile_temp, index=False)
                return {"repfile": report_file, "temp_file": repfile_temp, "sfx": sfx, "skip_other": False}
            else:
                return {"repfile": None, "temp_file": None, "sfx": None, "skip_other": True}

        @task
        def upload_rep1_v2(repdata):
            if not repdata['skip_other']:
                upload_rep(repdata)

        @task
        def upload_rep2_v2(repdata):
            if not repdata['skip_other']:
                upload_rep(repdata)

        @task
        def upload_rep3_v2(repdata):
            if not repdata['skip_other']:
                upload_rep(repdata)

        # вторая ветка
        load_result = load_data0_v2(unzipped)
        rep_data1_v2 = report1_v2(load_result)
        upload_rep1_v2(rep_data1_v2)
        rep_data2_v2 = report2_v2(load_result)
        upload_rep2_v2(rep_data2_v2)
        rep_data3_v2 = report3_v2(load_result)
        upload_rep3_v2(rep_data3_v2)

    tutorial_etl_dag = advde3_taskflow()
