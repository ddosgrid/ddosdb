FROM python:3

COPY ddosdb /app
WORKDIR /app
RUN pip install django-sslserver\
                pandas\
                nclib\
                elasticsearch\
                demjson\
                requests\
                django-debug-toolbar\
                psycopg2-binary;\
    mv /app/website/settings_local_docker.example.py /app/website/settings_local.py
RUN python manage.py migrate
EXPOSE 8000
CMD ["python" , "manage.py", "runserver", "--settings=website.settings-dev"]
