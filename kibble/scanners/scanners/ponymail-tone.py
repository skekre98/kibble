# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""
This is a Kibble scanner plugin for Apache Pony Mail sources.
"""
import time
import re

from kibble.scanners.utils import jsonapi, tone

title = "Tone/Mood Scanner plugin for Apache Pony Mail"
version = "0.1.0"
ROBITS = r"(git|gerrit|jenkins|hudson|builds|bugzilla)@"
MAX_COUNT = 250


def accepts(source):
    """ Test if source matches a Pony Mail archive """
    # If the source equals the plugin name, assume a yes
    if source["type"] == "ponymail":
        return True

    # If it's of type 'mail', check the URL
    if source["type"] == "mail":
        if re.match(r"(https?://.+)/list\.html\?(.+)@(.+)", source["sourceURL"]):
            return True

    # Default to not recognizing the source
    return False


def scan(KibbleBit, source):
    # Validate URL first
    url = re.match(r"(https?://.+)/list\.html\?(.+)@(.+)", source["sourceURL"])
    if not url:
        KibbleBit.pprint(
            "Malformed or invalid Pony Mail URL passed to scanner: %s"
            % source["sourceURL"]
        )
        source["steps"]["mail"] = {
            "time": time.time(),
            "status": "Could not parse Pony Mail URL!",
            "running": False,
            "good": False,
        }
        KibbleBit.updateSource(source)
        return

    if (
        not "watson" in KibbleBit.config
        and not "azure" in KibbleBit.config
        and not "picoapi" in KibbleBit.config
    ):
        KibbleBit.pprint(
            "No Watson/Azure/picoAPI creds configured, skipping tone analyzer"
        )
        return

    cookie = None
    if "creds" in source and source["creds"]:
        cookie = source["creds"].get("cookie", None)

    rootURL = re.sub(r"list.html.+", "", source["sourceURL"])
    query = {
        "query": {"bool": {"must": [{"term": {"sourceID": source["sourceID"]}}]}},
        "sort": [{"ts": "desc"}],
    }

    # Get an initial count of commits
    res = KibbleBit.broker.DB.search(
        index=KibbleBit.dbname, doc_type="email", body=query, size=MAX_COUNT * 4
    )
    ec = 0
    hits = []
    for hit in res["hits"]["hits"]:
        eml = hit["_source"]
        if not re.search(ROBITS, eml["sender"]):
            ec += 1
            if ec > MAX_COUNT:
                break
            if "mood" not in eml:
                emlurl = "%s/api/email.lua?id=%s" % (rootURL, eml["id"])
                KibbleBit.pprint("Fetching %s" % emlurl)
                rv = None
                try:
                    rv = jsonapi.get(emlurl, cookie=cookie)
                    if rv and "body" in rv:
                        hits.append([hit["_id"], rv["body"], eml])
                except Exception as err:
                    KibbleBit.pprint("Server error, skipping this email")

    bodies = []
    for hit in hits:
        body = hit[1]
        bid = hit[0]
        bodies.append(body)
    if bodies:
        if "watson" in KibbleBit.config:
            moods = tone.watsonTone(KibbleBit, bodies)
        elif "azure" in KibbleBit.config:
            moods = tone.azureTone(KibbleBit, bodies)
        elif "picoapi" in KibbleBit.config:
            moods = tone.picoTone(KibbleBit, bodies)
        if moods == False:
            KibbleBit.pprint("Hit rate limit, not trying further emails for now.")

        a = 0
        for hit in hits:
            mood = moods[a]
            bid = hit[0]
            eml = hit[2]
            a += 1
            eml["mood"] = mood
            hm = [0, "unknown"]
            for m, s in mood.items():
                if s > hm[0]:
                    hm = [s, m]
            print("Likeliest overall mood for %s: %s" % (bid, hm[1]))
            KibbleBit.index("email", bid, eml)
    else:
        KibbleBit.pprint("No emails to analyze")
    KibbleBit.pprint("Done with tone analysis")
