FROM python:3

RUN pip install django-sslserver\
                pandas\
                nclib\
                elasticsearch\
                demjson\
                requests\
                django-debug-toolbar\
                psycopg2-binary
    
COPY ddosdb /app
WORKDIR /app
RUN mv /app/website/settings_local_docker.example.py /app/website/settings_local.py; python manage.py migrate
EXPOSE 8000
CMD ["python" , "manage.py", "runserver", "--settings=website.settings-dev"]
