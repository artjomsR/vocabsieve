import json
import urllib.request
import requests
import os
import re
import time
from readmdict import MDX
from bs4 import BeautifulSoup
from typing import List, Dict
from .db import *
from pystardict import Dictionary
from .dictionary import *
from .xdxftransform import xdxf2html
from PyQt5.QtCore import QCoreApplication

def request(action, **params):
    return {'action': action, 'params': params, 'version': 6}

def parseMDX(path):
    mdx = MDX(path)
    stylesheet_lines = mdx.header[b'StyleSheet'].decode().splitlines()
    stylesheet_map = {}
    for line in stylesheet_lines:
        if line.isnumeric():
            number = int(line)
        else:
            stylesheet_map[number] = stylesheet_map.get(number, "") + line
    newdict = {} # This temporarily stores the new entries
    i = 0
    prev_headword = ""
    for item in mdx.items():
        headword, entry = item
        headword = headword.decode()
        entry = entry.decode()
        # The following applies the stylesheet
        if stylesheet_map:
            entry = re.sub(
                r'`(\d+)`', 
                lambda g: stylesheet_map.get(g.group().strip('`')), 
                entry
                )
        entry = entry.replace("\n", "").replace("\r", "")
        # Using newdict.get would become incredibly slow,
        # here we exploit the fact that they are alphabetically ordered
        if prev_headword == headword:
            newdict[headword] = newdict[headword] + entry
        else:
            newdict[headword] = entry
        prev_headword = headword
    return newdict


def invoke(action, server, **params):
    requestJson = json.dumps(request(action, **params)).encode('utf-8')
    response = json.load(
        urllib.request.urlopen(
            urllib.request.Request(
                server, requestJson)))
    if len(response) != 2:
        raise Exception('response has an unexpected number of fields')
    if 'error' not in response:
        raise Exception('response is missing required error field')
    if 'result' not in response:
        raise Exception('response is missing required result field')
    if response['error'] is not None:
        raise Exception(response['error'])
    return response['result']


def getDeckList(server) -> list:
    result = invoke('deckNames', server)
    return list(result)


def getNoteTypes(server) -> list:
    result = invoke('modelNames', server)
    return list(result)


def getFields(server, name) -> list:
    result = invoke('modelFieldNames', server, modelName=name)
    return list(result)


def addNote(server, content) -> int:
    result = invoke('addNote', server, note=content)
    return int(result)


def addNotes(server, content) -> List[int]:
    result = invoke('addNotes', server, notes=content)
    return list(result)


def getVersion(server) -> str:
    result = invoke('version', server)
    return str(result)


def is_json(myjson) -> bool:
    if not myjson.startswith("{"):
        return False
    try:
        json_object = json.loads(myjson)
        json_object['word']
        json_object['sentence']
    except ValueError as e:
        return False
    except Exception as e:
        print(e)
        return False
    return True


def failed_lookup(word, settings) -> str:
    return str("<b>Definition for \"" + str(word) + "\" not found.</b><br>Check the following:<br>" +\
        "- Language setting (Current: " + settings.value("target_language", 'en') + ")<br>" +\
        "- Is the correct word being looked up?<br>" +\
        "- Are you connected to the Internet?<br>" +\
        "Otherwise, then " + settings.value("dict_source", "Wiktionary (English)") + 
        " probably just does not have this word listed.")


def is_oneword(s) -> bool :
    return len(s.split()) == 1


def dictinfo(path) -> Dict[str,str]:
    "Get information about dictionary from file path"
    basename, ext = os.path.splitext(path)
    basename = os.path.basename(basename)
    if os.path.isdir(path):
        return {"type": "audiolib", "basename": basename, "path": path}
    if ext not in [".json", ".ifo", ".mdx"]:
        raise NotImplementedError("Unsupported format")
    elif ext == ".json":
        with open(path, encoding="utf-8") as f:
            try:
                d = json.load(f)
                if isinstance(d, list):
                    if isinstance(d[0], str):
                        return {
                            "type": "freq",
                            "basename": basename,
                            "path": path}
                    return {
                        "type": "migaku",
                        "basename": basename,
                        "path": path}
                elif isinstance(d, dict):
                    return {"type": "json", "basename": basename, "path": path}
            except Exception:
                raise IOError("Reading failed")
    elif ext == ".ifo":
        return {"type": "stardict", "basename": basename, "path": path}
    elif ext == ".mdx":
        return {"type": "mdx", "basename": basename, "path": path}

def dictimport(path, dicttype, lang, name) -> None:
    "Import dictionary from file to database"
    if dicttype == "stardict":
        stardict = Dictionary(os.path.splitext(path)[0], in_memory=True)
        d = {}
        if stardict.ifo.sametypesequence == 'x':
            for key in stardict.idx.keys():
                d[key] = xdxf2html(stardict.dict[key])
        else:
            for key in stardict.idx.keys():
                d[key] = stardict.dict[key]
        dictdb.importdict(d, lang, name)
    elif dicttype == "json":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            dictdb.importdict(d, lang, name)
    elif dicttype == "migaku":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            d = {}
            for item in data:
                d[item['term']] = item['definition']
            dictdb.importdict(d, lang, name)
    elif dicttype == "freq":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            d = {}
            for i, word in enumerate(data):
                d[word] = i + 1
            dictdb.importdict(d, lang, name)
    elif dicttype == "audiolib":
        # Audios will be stored as a serialized json list
        filelist = []
        d = {}
        for root, dirs, files in os.walk(path):
            for item in files:
                filelist.append(
                    os.path.relpath(
                        os.path.join(
                            root, item), path))
        print(len(filelist), "audios selected.")
        for item in filelist:
            headword = os.path.basename(os.path.splitext(item)[0]).lower()
            if not d.get(headword):
                d[headword] = [item]
            else:
                d[headword].append(item)
        for word in d.keys():
            d[word] = json.dumps(d[word])
        dictdb.importdict(d, lang, name)
    elif dicttype == 'mdx':
        d = parseMDX(path)
        dictdb.importdict(d, lang, name)


def dictdelete(name) -> None:
    dictdb.deletedict(name)
