#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (C) 2016 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#     Luis Cañas-Díaz <lcanas@bitergia.com>
#     Alvaro del Castillo <acs@bitergia.com>
#

import json
import logging
import re

from grimoire_elk.elk import get_ocean_backend
from grimoire_elk.utils import get_connector_from_name, get_elastic
from grimoire_elk.enriched.utils import grimoire_con

logger = logging.getLogger(__name__)


class Task():
    """ Basic class shared by all tasks """

    NO_BACKEND_FIELDS = ['enriched_index', 'raw_index', 'es_collection_url',
                         'collect', 'pair-programming', 'fetch-archive', 'studies',
                         'node_regex', 'arthur_delay', 'enrich_interval']
    PARAMS_WITH_SPACES = ['blacklist-jobs']

    def __init__(self, config):
        self.backend_section = None
        self.config = config
        self.conf = config.get_conf()
        self.db_sh = self.conf['sortinghat']['database']
        self.db_user = self.conf['sortinghat']['user']
        self.db_password = self.conf['sortinghat']['password']
        self.db_host = self.conf['sortinghat']['host']
        self.grimoire_con = grimoire_con(conn_retries=12)  # 30m retry

    @staticmethod
    def anonymize_url(url):
        anonymized = re.sub('^http.*@', 'http://', url)

        return anonymized

    @staticmethod
    def load_aliases_from_json(aliases_json):
        with open(aliases_json, 'r') as f:
            try:
                aliases = json.load(f)
            except Exception as ex:
                logger.error(ex)
                raise

        return aliases

    def is_backend_task(self):
        """
        Returns True if the Task is executed per backend.
        i.e. SortingHat unify is not executed per backend.
        """
        return True

    def execute(self):
        """ Execute the Task """
        logger.debug("A bored task. It does nothing!")

    @classmethod
    def get_backend(self, backend_section):
        # To support the same data source with different configs
        # remo:activitites is like remo but with an extra param
        # category = activity
        backend = backend_section.split(":")[0]
        return backend

    def set_backend_section(self, backend_section):
        self.backend_section = backend_section

    def _compose_p2o_params(self, backend_section, repo):
        # get p2o params included in the projects list
        params = {}

        backend = self.get_backend(backend_section)
        connector = get_connector_from_name(backend)
        ocean = connector[1]

        # First add the params from the URL, which is backend specific
        params = ocean.get_p2o_params_from_url(repo)

        return params

    def _compose_arthur_params(self, backend_section, repo):
        # Params for the backends must be in a dictionary for arthur

        params = {}

        backend = self.get_backend(backend_section)
        connector = get_connector_from_name(backend)
        ocean = connector[1]

        # First add the params from the URL, which is backend specific
        params.update(ocean.get_arthur_params_from_url(repo))

        # Now add the backend params included in the config file
        for p in self.conf[backend_section]:
            if p in self.NO_BACKEND_FIELDS:
                # These params are not for the perceval backend
                continue
            if self.conf[backend_section][p]:
                # Command line - in param is converted to _ in python variable
                p_ = p.replace("-", "_")
                if p in self.PARAMS_WITH_SPACES:
                    # '--blacklist-jobs', 'a', 'b', 'c'
                    # 'a', 'b', 'c' must be added as items in the list
                    list_params = self.conf[backend_section][p].split()
                    params[p_] = list_params
                else:
                    params[p_] = self.conf[backend_section][p]

        return params

    def _compose_perceval_params(self, backend_section, repo):
        backend = self.get_backend(backend_section)
        connector = get_connector_from_name(backend)
        ocean = connector[1]

        # First add the params from the URL, which is backend specific
        params = ocean.get_perceval_params_from_url(repo)

        # Now add the backend params included in the config file
        for p in self.conf[backend_section]:
            if p in self.NO_BACKEND_FIELDS:
                # These params are not for the perceval backend
                continue

            section_param = self.conf[backend_section][p]
            if not section_param:
                logger.warning("Empty section %s", p)
                continue

            # If param is boolean, no values must be added
            if type(section_param) == bool:
                params.append("--" + p) if section_param else None
            elif type(section_param) == list:
                # '--blacklist-jobs', 'a', 'b', 'c'
                # 'a', 'b', 'c' must be added as items in the list
                params.append("--" + p)
                list_params = section_param
                params += list_params
            else:
                params.append("--" + p)
                params.append(str(section_param))

        return params

    def _get_collection_url(self):
        es_col_url = self.conf['es_collection']['url']
        if self.backend_section and self.backend_section in self.conf:
            if 'es_collection_url' in self.conf[self.backend_section]:
                es_col_url = self.conf[self.backend_section]['es_collection_url']
        else:
            logger.warning("No config for the backend %s", self.backend_section)
        return es_col_url

    def _get_enrich_backend(self):
        db_projects_map = None
        json_projects_map = None
        clean = False
        connector = get_connector_from_name(self.get_backend(self.backend_section))

        if 'projects_file' in self.conf['projects']:
            json_projects_map = self.conf['projects']['projects_file']

        enrich_backend = connector[2](self.db_sh, db_projects_map, json_projects_map,
                                      self.db_user, self.db_password, self.db_host)
        elastic_enrich = get_elastic(self.conf['es_enrichment']['url'],
                                     self.conf[self.backend_section]['enriched_index'],
                                     clean, enrich_backend)
        enrich_backend.set_elastic(elastic_enrich)

        if 'github' in self.conf.keys() and \
            'backend_token' in self.conf['github'].keys() and \
            self.get_backend(self.backend_section) == "git":

            gh_token = self.conf['github']['backend_token']
            enrich_backend.set_github_token(gh_token)

        if 'unaffiliated_group' in self.conf['sortinghat']:
            enrich_backend.unaffiliated_group = self.conf['sortinghat']['unaffiliated_group']

        return enrich_backend

    def _get_ocean_backend(self, enrich_backend):
        backend_cmd = None

        no_incremental = False
        clean = False

        from .task_projects import TaskProjects
        repos = TaskProjects.get_repos_by_backend_section(self.backend_section)
        if len(repos) == 1:
            # Support for filter raw when we have one repo
            repo = repos[0]
            p2o_args = self._compose_p2o_params(self.backend_section, repo)
            filter_raw = p2o_args['filter-raw'] if 'filter-raw' in p2o_args else None
            filters_raw_prefix = p2o_args['filter-raw-prefix'] if 'filter-raw-prefix' in p2o_args else None

            if filter_raw or filters_raw_prefix:
                logger.info("Using %s %s for getting identities from raw",
                            filter_raw, filters_raw_prefix)
            ocean_backend = get_ocean_backend(backend_cmd, enrich_backend, no_incremental,
                                              filter_raw, filters_raw_prefix)
        else:
            ocean_backend = get_ocean_backend(backend_cmd, enrich_backend, no_incremental)

        elastic_ocean = get_elastic(self._get_collection_url(),
                                    self.conf[self.backend_section]['raw_index'],
                                    clean, ocean_backend)
        ocean_backend.set_elastic(elastic_ocean)

        return ocean_backend

    def es_version(self, url):
        """Get Elasticsearch version.

        Get the version of Elasticsearch. This is useful because
        Elasticsearch and Kibiter are paired (same major version for 5, 6).

        :param url: Elasticseearch url hosting Kibiter indices
        :returns:   major version, as string
        """

        try:
            res = self.grimoire_con.get(url)
            res.raise_for_status()
            major = res.json()['version']['number'].split(".")[0]
        except Exception:
            logger.error("Error retrieving Elasticsearch version: " + url)
            raise
        return major

    @staticmethod
    def retain_data(retention_time, es_url, index):
        elastic = get_elastic(es_url, index)
        elastic.delete_items(retention_time)
