import math
import os
import time
from smtplib import SMTPException
import demjson
import requests
from datetime import datetime
from urllib.parse import urlencode

import pprint
import pandas as pd
import numpy as np
import re

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission, User
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.http import HttpResponse
from django.http import JsonResponse
from django.http import FileResponse
from django.shortcuts import render, redirect, reverse
from django.views.decorators.csrf import csrf_exempt
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import RequestError, NotFoundError

from oauth2_provider.views.generic import ProtectedResourceView
from oauth2_provider.decorators import protected_resource
from oauth2_provider.models import AccessToken

from ddosdb.enrichment.team_cymru import TeamCymru
from ddosdb.models import Query, AccessRequest, Blame, FileUpload


def index(request):
    print(request)
    context = {}
    return HttpResponse(render(request, "ddosdb/index.html", context))


def about(request):
    context = {}
    return HttpResponse(render(request, "ddosdb/about.html", context))


def help_page(request):
    context = {}
    return HttpResponse(render(request, "ddosdb/help.html", context))


def signin(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            if "next" in request.GET:
                return redirect(request.GET["next"])
            else:
                return redirect("index")
        else:
            context = {"failed": True}
    else:
        context = {}

    return HttpResponse(render(request, "ddosdb/login.html", context))


def request_access(request):
    context = {
        "error": False,
        "success": False
    }

    if request.method == "POST":
        captcha_verify = requests.post("https://www.google.com/recaptcha/api/siteverify",
                                       data={"secret": settings.RECAPTCHA_PRIVATE_KEY,
                                             "response": request.POST["g-recaptcha-response"]})
        captcha_okay = demjson.decode(captcha_verify.text)["success"]

        if captcha_okay:
            access_request = AccessRequest(first_name=request.POST["first-name"],
                                           last_name=request.POST["last-name"],
                                           email=request.POST["email"],
                                           institution=request.POST["institution"],
                                           purpose=request.POST["purpose"])

            try:
                send_mail("DDoSDB Access Request",
                          """
                First name: {first_name}
                Last name: {last_name}
                Email: {email}
                Institution: {institution}
                Purpose: {purpose}
                """.format(first_name=access_request.first_name,
                           last_name=access_request.last_name,
                           email=access_request.email,
                           institution=access_request.institution,
                           purpose=access_request.purpose),
                          "noreply@ddosdb.org",
                          [settings.ACCESS_REQUEST_EMAIL])

                access_request.save()

                context["success"] = True
            except (SMTPException, ConnectionRefusedError) as e:
                context["error"] = e
        else:
            context["error"] = "Invalid captcha"

    return HttpResponse(render(request, "ddosdb/request-access.html", context))


@login_required()
def account(request):
    user: User = request.user
    context = {
        "user": user,
        "permissions": user.get_all_permissions(),
        "success": "",
        "error": ""
    }

    if request.method == "POST":
        if "email" in request.POST:
            email = request.POST["email"].strip()

            if email != user.email:
                if User.objects.filter(email=email).exists():
                    context["error"] = "There already is a user with this email address"
                else:
                    try:
                        validate_email(email)
                        user.email = email
                        user.username = email
                        user.save()

                        user = authenticate(request, username=email)
                        context["success"] = "Successfully changed your email address"
                    except ValidationError:
                        context["error"] = "This is not a valid email address"

        elif "new-password" in request.POST:
            if request.POST["new-password"] == request.POST["new-password2"]:
                if user.check_password(request.POST["current-password"]):
                    user.set_password(request.POST["new-password"])
                    user.save()

                    user = authenticate(request, username=user.username, password=request.POST["new-password"])
                    context["success"] = "Successfully changed your password"
                else:
                    context["error"] = "The current password is incorrect"
            else:
                context["error"] = "The passwords are not the same"

    return HttpResponse(render(request, "ddosdb/account.html", context))


@login_required()
def signout(request):
    logout(request)
    return redirect("index")

@login_required()
def query(request):
    start = time.time()
    context = {
        "results": [],
        "comments": {},
        "q": "",
        "p": 1,
        "o": "_score",
        "pages": range(1, 1),
        "amount": 0,
        "error": "",
        "time": 0
    }

    if "q" in request.GET:
        if "p" in request.GET:
            context["p"] = int(request.GET["p"])
        if "o" in request.GET:
            context["o"] = request.GET["o"]

        q = context["q"] = request.GET["q"]

        if context["p"] == 1:
            data_query = Query(query=q, user_id=request.user.id)
            data_query.save()

        try:
            offset = 10 * (context["p"] - 1)

            es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)
            response = es.search(index="ddosdb", q=q, from_=offset, size=10, sort=context["o"])
            context["time"] = time.time() - start

            results = [x["_source"] for x in response["hits"]["hits"]]
            context["amount"] = response["hits"]["total"]
#            context["pages"] = range(1, int(math.ceil(context["amount"] / 10)) + 1)
            context["pages"] = ""

#            for x in results:
#                if "comments" in x:
#                    context["comments"][x["key"]] = x.pop("comments", None)

            def clean_result(x):
                # Remove the start_timestamp attribute (if it exists)
                x.pop("start_timestamp", None)

#                for y in x["src_ips"]:
#                    y.pop("as", None)
#                    y.pop("cc", None)


                return x

            results = map(clean_result, results)
            results = list(results)

            if request.user.has_perm("ddosdb.view_blame"):
                for result in results:
                    try:
                        result["blame"] = Blame.objects.get(key=result["key"]).to_dict()
                    except ObjectDoesNotExist:
                        pass

            context["results"] = results
        except (SyntaxError, RequestError) as e:
            context["error"] = "Invalid query: " + str(e)

    return HttpResponse(render(request, "ddosdb/query.html", context))


@protected_resource()
def profileInfo(request):
    user = extract_user_info_from_authtoken(request)
    context = {
        "id": request.resource_owner.id,
        "username": request.resource_owner.username,
        "email": request.resource_owner.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }

    return JsonResponse(context)


@protected_resource()
def queryJSON_API(request):
    start = time.time()
    context = {
        "results": [],
        "comments": {},
        "q": "",
        "p": 1,
        "o": "_score",
        "pages": range(1, 1),
        "amount": 0,
        "error": "",
        "time": 0
    }

    if "q" in request.GET:
        if "p" in request.GET:
            context["p"] = int(request.GET["p"])
        if "o" in request.GET:
            context["o"] = request.GET["o"]

        q = context["q"] = request.GET["q"]

        if context["p"] == 1:
            data_query = Query(query=q, user_id=request.resource_owner.id)
            data_query.save()

        try:
            offset = 10 * (context["p"] - 1)

            es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)
            response = es.search(index="ddosdb", q=q, from_=offset, size=10, sort=context["o"])
            context["time"] = time.time() - start

            results = [x["_source"] for x in response["hits"]["hits"]]
            context["amount"] = response["hits"]["total"]
#            context["pages"] = range(1, int(math.ceil(context["amount"] / 10)) + 1)
            context["pages"] = ""

            #            for x in results:
            #                if "comments" in x:
            #                    context["comments"][x["key"]] = x.pop("comments", None)

            def clean_result(x):
                # Remove the start_timestamp attribute (if it exists)
                x.pop("start_timestamp", None)

                #                for y in x["src_ips"]:
                #                    y.pop("as", None)
                #                    y.pop("cc", None)


                return x

            results = map(clean_result, results)
            results = list(results)

            if request.resource_owner.has_perm("ddosdb.view_blame"):
                for result in results:
                    try:
                        result["blame"] = Blame.objects.get(key=result["key"]).to_dict()
                    except ObjectDoesNotExist:
                        pass

            context["results"] = results
        except (SyntaxError, RequestError) as e:
            context["error"] = "Invalid query: " + str(e)

    print(context)
    return JsonResponse(context)

@login_required()
def queryJSON(request):
    start = time.time()
    context = {
        "results": [],
        "comments": {},
        "q": "",
        "p": 1,
        "o": "_score",
        "pages": range(1, 1),
        "amount": 0,
        "error": "",
        "time": 0
    }

    if "q" in request.GET:
        if "p" in request.GET:
            context["p"] = int(request.GET["p"])
        if "o" in request.GET:
            context["o"] = request.GET["o"]

        q = context["q"] = request.GET["q"]

        if context["p"] == 1:
            data_query = Query(query=q, user_id=request.user.id)
            data_query.save()

        try:
            offset = 10 * (context["p"] - 1)

            es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)
            response = es.search(index="ddosdb", q=q, from_=offset, size=10, sort=context["o"])
            context["time"] = time.time() - start

            results = [x["_source"] for x in response["hits"]["hits"]]
            context["amount"] = response["hits"]["total"]
            context["pages"] = range(1, int(math.ceil(context["amount"] / 10)) + 1)

#            for x in results:
#                if "comments" in x:
#                    context["comments"][x["key"]] = x.pop("comments", None)

            def clean_result(x):
                # Remove the start_timestamp attribute (if it exists)
                x.pop("start_timestamp", None)

#                for y in x["src_ips"]:
#                    y.pop("as", None)
#                    y.pop("cc", None)


                return x

            results = map(clean_result, results)
            results = list(results)

            if request.user.has_perm("ddosdb.view_blame"):
                for result in results:
                    try:
                        result["blame"] = Blame.objects.get(key=result["key"]).to_dict()
                    except ObjectDoesNotExist:
                        pass

            context["results"] = results
        except (SyntaxError, RequestError) as e:
            context["error"] = "Invalid query: " + str(e)

    return HttpResponse(render(request, "ddosdb/query.html", context))


@login_required()
def compare(request):
    items = {}
    similarities = {}
    es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)

    for key in request.GET.getlist("key"):
        items[key] = es.get(index="ddosdb", doc_type="_doc", id=key)["_source"]

    # for x in es.search(index="ddosdb", q="pcap", size=50)["hits"]["hits"]:
    #     items[x["_id"]] = x["_source"]

    for key in items:
        similarities[key] = {}
        my_set = set([x["ip"] for x in items[key]["src_ips"]])
        for other_key in items:
            other_set = set([x["ip"] for x in items[other_key]["src_ips"]])
            similarities[key][other_key] = {
                "percentage": len(my_set.intersection(other_set)) / len(other_set),
                "fraction": str(len(my_set.intersection(other_set))) + "/" + str(len(other_set))
            }

    context = {
        "similarities": similarities,
        "items": items
    }
    return HttpResponse(render(request, "ddosdb/compare.html", context))


@login_required()
def fingerprint(request, key):
    file = settings.RAW_PATH + key + ".json"
    if os.path.isfile(file):
        #response = HttpResponse(content_type="application/json")
        #response["X-Sendfile"] = file
        #response["Content-Disposition"] = "attachment; filename=" + key + ".json"
        #return response
        return FileResponse(open(file, 'rb'))
    else:
        return HttpResponse("File not found")


@login_required()
def attack_trace(request, key):
    file = ""
    for file_path in os.listdir(settings.RAW_PATH):
        filename, file_extension = os.path.splitext(file_path)
        if filename == key and not file_extension == ".json":
            file = file_path
            break

    if file != "":
        response = FileResponse(open(settings.RAW_PATH + file, 'rb'))
#        response = HttpResponse(content_type="application/octet-stream")
#        response["X-Sendfile"] = settings.RAW_PATH + file
#        response["Content-Disposition"] = "attachment; filename=" + file
        return response
    else:
        return HttpResponse("File not found")

@protected_resource()
def attack_trace_api(request, key):
    file = ""
    for file_path in os.listdir(settings.RAW_PATH):
        filename, file_extension = os.path.splitext(file_path)
        if filename == key and file_extension == ".pcap":
            file = file_path
            break

    if file != "":
        response = FileResponse(open(settings.RAW_PATH + file, 'rb'))
#        response = HttpResponse(content_type="application/octet-stream")
#        response["X-Sendfile"] = settings.RAW_PATH + file
#        response["Content-Disposition"] = "attachment; filename=" + file
        return response
    else:
        return HttpResponse("File not found")

@login_required()
def filter_rules(request, key):
    file = ""
    for file_path in os.listdir(settings.RAW_PATH):
        filename, file_extension = os.path.splitext(file_path)
        if filename == key and file_extension == ".iptables":
            file = file_path
            break

    if file != "":
        response = FileResponse(open(settings.RAW_PATH + file, 'rb'))
#        response = HttpResponse(content_type="application/octet-stream")
#        response["X-Sendfile"] = settings.RAW_PATH + file
#        response["Content-Disposition"] = "attachment; filename=" + file
        return response
    else:
        return HttpResponse("File not found")

def extract_user_info_from_authtoken(request):
    app_tk = request.META["HTTP_AUTHORIZATION"]
    m = re.search('(Bearer)(\s)(.*)', app_tk)
    app_tk = m.group(3)
    acc_tk = AccessToken.objects.get(token=app_tk)
    return acc_tk.user

@protected_resource()
@csrf_exempt
def upload_api(request):
    if request.method == "POST":
        username = request.resource_owner.username
        filename = request.META["HTTP_X_FILENAME"]

        print("user:{} - filename:{}".format(username, filename))
        user = extract_user_info_from_authtoken(request)

        return index_uploaded_files(request, user, filename)
    else:
        response = HttpResponse()
        response.status_code = 405
        return response

@protected_resource()
@csrf_exempt
def upload_filter_rules(request):
    iptables_script_supplied = "iptables" in request.FILES
    filename_defined = "HTTP_X_FILENAME" in request.META
    if request.method == "POST" and iptables_script_supplied and filename_defined:
        # optional iptables upload
        filename = request.META["HTTP_X_FILENAME"]
        iptables_fp = open(settings.RAW_PATH + filename + ".iptables", "wb+")
        iptables_file = request.FILES["iptables"]
        for chunk in iptables_file.chunks():
            iptables_fp.write(chunk)

        iptables_fp.close()
        response = HttpResponse()
        response.status_code = 201
        return response
    else:
        response = HttpResponse()
        response.status_code = 405
        return response


@csrf_exempt
def upload_file(request):
    if request.method == "POST":
        if not all (k in request.META for k in ("HTTP_X_USERNAME","HTTP_X_PASSWORD","HTTP_X_FILENAME")):
            response = HttpResponse()
            response.status_code = 401
            response.reason_phrase = "Invalid credentials or no permission"
            return response

        username = request.META["HTTP_X_USERNAME"]
        password = request.META["HTTP_X_PASSWORD"]
        filename = request.META["HTTP_X_FILENAME"]
        user = authenticate(request, username=username, password=password)

        print("user:{} - filename:{}".format(username, filename))

        return index_uploaded_files(request, user, filename)

    else:
        response = HttpResponse()
        response.status_code = 405
        return response

def index_uploaded_files(request, user, filename):
    if user is None or not user.has_perm("ddosdb.upload_fingerprint"):
        response = HttpResponse()
        response.status_code = 403
        response.reason_phrase = "Invalid credentials or no permission to upload fingerprints"
        return response

    try:
        os.remove(settings.RAW_PATH + filename + ".json")
        os.remove(settings.RAW_PATH + filename + ".pcap")
    except IOError:
        pass

    # JSON enrichment
    json_content = request.FILES["json"].read()
    data = demjson.decode(json_content)
    # Add key if not exists
    if "key" not in data:
        data["key"] = filename

    if "dst_ports" in data:
        data["dst_ports"] = [x for x in data["dst_ports"] if not math.isnan(x)]
    if "src_ports" in data:
        data["src_ports"] = [x for x in data["src_ports"] if not math.isnan(x)]

    # Enrich it all a bit
    data["amplifiers_size"] = 0
    data["attackers_size"] = 0

    if "src_ips" in data:
        data["src_ips_size"] = len(data["src_ips"])

    if "amplifiers" in data:
        data["amplifiers_size"] = len(data["amplifiers"])

    if "attackers" in data:
        data["attackers_size"] = len(data["attackers"])

    data["ips_involved"] = data["amplifiers_size"] + data["attackers_size"]

#        data["comment"] = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum."
    data["comment"] = ""

    # add username of submitter as well.
    # Probably best to have an optional separate field for contact information
    data["submitter"] = user.username

    # Add the timestamp it was submitted as well.
    # Usefull for ordering in overview page.

    data["submit_timestamp"] = datetime.utcnow()

#        else:
#            if "amplifiers" in data:
#                data["src_ips"]      = data["amplifiers"]
#                data["src_ips_size"] = len(data["src_ips"])
#            else:
#                data["src_ips"]      = []
#                data["src_ips_size"] = 0


    # Bear in mind that the data format may change. Hence the order of these steps is important.
    # Enrich with ASN
    # data = (TeamCymru(data)).parse()
    print("Enrichment with AS # disabled")
    # Enrich with something
    # data = (Something(data)).parse()


    # JSON upload
    demjson.encode_to_file(settings.RAW_PATH + filename + ".json", data)

    # JSON database insert
    es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)

    try:
        es.delete(index="ddosdb", doc_type="_doc", id=filename, request_timeout=500)
    except NotFoundError:
        pass
    except:
        print("Could not setup a connection to Elasticsearch")
        response = HttpResponse()
        response.status_code = 503
        response.reason_phrase = "Database unavailable"
        return response

    try:
        es.index(index="ddosdb", doc_type="_doc", id=filename, body=data, request_timeout=500)
    except RequestError as e:
        response = HttpResponse()
        response.status_code = 400
        response.reason_phrase = str(e)
        return response

    # PCAP upload
    pcap_fp = open(settings.RAW_PATH + filename + ".pcap", "wb+")
    pcap_file = request.FILES["pcap"]
    for chunk in pcap_file.chunks():
        pcap_fp.write(chunk)

    pcap_fp.close()

    # Register record
    file_upload = FileUpload()
    file_upload.user = user
    file_upload.filename = filename
    file_upload.save()

    response = HttpResponse()
    response.status_code = 201
    return response









@login_required()
def overview(request):

    pp = pprint.PrettyPrinter(indent=4)

    user: User = request.user

    start = time.time()
    context = {
        "user": user,
        "permissions": user.get_all_permissions(),
        "results": [],
        "q": "",
        "p": 1,
        "o": "key",
        "so": "asc",
        "son": "desc",
        "error": "",
        "time": 0
    }

    if "q" in request.GET:
        context["q"] = request.GET["q"]
    if "o" in request.GET:
        context["o"] = request.GET["o"]
    if "so" in request.GET:
        context["so"] = request.GET["so"]
    if "son" in request.GET:
        context["son"] = request.GET["son"]

    try:
        #offset = 10 * (context["p"] - 1)
        es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)
        context["headers"] = {
#            "multivector_key"   : "multivector",
            "key"               : "key",
            "start_time"        : "start time",
            "duration_sec"      : "duration (seconds)",
#            "total_packets"     : "# packets",
#            "amplifiers_size"    : "IP's involved",
            "ips_involved"      : "IP's involved",
            "avg_bps"           : "bits/second",
            "avg_pps"           : "packets/second",
            "total_dst_ports"   : "# ports",
            "submit_timestamp"  : "submitted at",
            "submitter"         : "submitted by",
            "comment"           : "comment",
        }
        source = ','.join(list(context["headers"].keys()))

        q = "*"
        if (context["q"]):
            q = context["q"]

        response = es.search(index="ddosdb", q=q, size=10000,  _source=source)

        print(source)


        context["time"] = time.time() - start

        results = [x["_source"] for x in response["hits"]["hits"]]

#        pp.pprint(results)
        # Only do this if there are actual results...
        # and more than one, since one result does not need sorting
        if len(results) > 1 :
            df = pd.DataFrame.from_dict(results)
            o = [context["o"]][0]

            # Do a special sort if the column to sort by is 'submitter'
            # Since people can put e-mail addresses in starting with upper/lower case
            # Change if statement to the following if you want to sort case insensitive
            # on any column with string values: if df.dtypes[o] == np.object:
            if o == "submitter":
                onew = o+"_tmp"
                df[onew] = df[o].str.lower()
                df.sort_values(by=onew, ascending=(context["so"]=="asc"), inplace=True)
                del df[onew]
            else:
                df.sort_values(by=o, ascending=(context["so"]=="asc"), inplace=True)

            # Make sure some columns are shown as int
            df = df.astype({"ips_involved": int})

            context["results"] = df.to_dict(orient='records')
        else:
            context["results"] = results

    except (SyntaxError, RequestError) as e:
        context["error"] = "Invalid query: " + str(e)

    return HttpResponse(render(request, "ddosdb/overview.html", context))


# Authenticate the user, then return a list of permissions
# Or an error if the user cannot be authenticated of course

#@login_required()
def my_permissions(request):

    if request.method == "GET":

        if not all (k in request.META for k in ("HTTP_X_USERNAME","HTTP_X_PASSWORD")):
            response = HttpResponse()
            response.status_code = 401
            response.reason_phrase = "Invalid credentials or no permission"
            return response

        username = request.META["HTTP_X_USERNAME"]
        password = request.META["HTTP_X_PASSWORD"]

        user = authenticate(request, username=username, password=password)

        if user is None:
            response = HttpResponse()
            response.status_code = 401
            response.reason_phrase = "Invalid credentials or no permission"
            return response

        user_perms = user.get_user_permissions()
        group_perms = user.get_group_permissions()

        # make a combined set (a set cannot contain duplicates)
        permissions = user_perms | group_perms

        # Now filter out everything but the ddosdb.* permissions
        ddosdb_permissions = []
        for p in permissions:
            if p.startswith("ddosdb."):
                ddosdb_permissions.append(p)

        return JsonResponse({str(user) : ddosdb_permissions}, safe=False)


@login_required()
def edit_comment(request):

    pp = pprint.PrettyPrinter(indent=4)

    user: User = request.user
    context = {
        "user": user,
        "permissions": user.get_all_permissions(),
    }
    user_perms = user.get_user_permissions()
    group_perms = user.get_group_permissions()

    # make a combined set (a set cannot contain duplicates)
    permissions = user_perms | group_perms

    key = ""


    if request.method == "GET":
        # Get the key from the request.
        if "key" in request.GET:
            key = request.GET["key"]
            context["key"] = key
        else:
            return redirect('overview')

        es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)
        try:
            fp = es.search(index="ddosdb", q="key:{}".format(key), size=1)
        except:
            print("Could not setup a connection to Elasticsearch")
            response = HttpResponse()
            response.status_code = 503
            response.reason_phrase = "Database unavailable"
            return response

        results = fp["hits"]["hits"][0]["_source"]
        context["node"] = results
        return HttpResponse(render(request, "ddosdb/edit-comment.html", context))

    elif request.method == "POST":
        key = request.POST["key"]

        es = Elasticsearch(hosts=settings.ELASTICSEARCH_HOSTS)
        try:
            result = es.search(index="ddosdb", q="key:{}".format(key), size=1)
            fp = result["hits"]["hits"][0]["_source"]
            fp["comment"] = request.POST["comment"]
            es.delete(index="ddosdb", doc_type="_doc", id=key, request_timeout=500)
        except NotFoundError:
            print("NotFoundError for {}".format(key))
            pass
        except:
            print("Could not setup a connection to Elasticsearch")
            response = HttpResponse()
            response.status_code = 503
            response.reason_phrase = "Database unavailable"
            return response

        es.index(index="ddosdb", doc_type="_doc", id=key, body=fp, request_timeout=500)

#        base_url = reverse('edit-comment')
#        query_string =  urlencode({'key': key})
#        url = '{}?{}'.format(base_url, query_string)
#        return redirect(url)
        time.sleep(1)
        return redirect("overview")
