#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AUTHOR

    Pedro Cerqueira
    github: @pedrorvc

    Rafael Mamede
    github: @rfm-targa

DESCRIPTION

"""


import os
import sys
import json
import time
import pickle
import zipfile
import hashlib
import argparse
import requests
import itertools
import datetime as dt
import multiprocessing
import concurrent.futures
from getpass import getpass
from collections import Counter
from SPARQLWrapper import SPARQLWrapper, JSON

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.Data.CodonTable import TranslationError

from utils import constants as cnst
from utils import auxiliary_functions as aux

from urllib3.exceptions import InsecureRequestWarning

# Suppress only the single warning from urllib3 needed.
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


virtuoso_server = SPARQLWrapper('http://sparql.uniprot.org/sparql')


def retrieve_schema_info(schemas_list, schema_desc):
    """
    """

    schema_exists = False
    for s in schemas_list:
        current_desc = s['name']['value']
        if current_desc == schema_desc:
            schema_exists = True
            schema_url = s['schemas']['value']
            schema_id = schema_url.split('/')[-1]

    if schema_exists:
        return [schema_url, schema_id]
    else:
        return 404


def determine_upload(local_schema_loci, ns_schema_loci,
                     ns_schema_locid_map, local_path,
                     base_url, headers_get):
    """
    """

    missing = []
    incomplete = []
    for locus in local_schema_loci:
        local_locus = locus

        if local_locus in ns_schema_loci:
            local_file = os.path.join(local_path, locus)
            local_sequences = [str(rec.seq)
                               for rec in SeqIO.parse(local_file, 'fasta')]

            ns_uri = ns_schema_locid_map[locus][0]
            ns_locus_id = ns_uri.split('/')[-1]
            ns_info = aux.simple_get_request(base_url, headers_get,
                                             ['loci', ns_locus_id, 'fasta'])
            ns_sequences = [seq['nucSeq']['value']
                            for seq in ns_info.json()['Fasta']]

            local_set = set(local_sequences)
            ns_set = set(ns_sequences)

            if len(ns_set) > len(local_set):
                sys.exit('A locus in the NS has more sequences '
                         'than the local locus.\nLocal schema '
                         'is not the original.')

            ns_diff = ns_set - local_set
            if len(ns_diff) > 0:
                sys.exit('A locus in the NS has sequences '
                         'that are not in the local locus.'
                         '\nLocal schema is not the original.')

            local_diff = local_set - ns_set
            if len(local_diff) > 0:
                incomplete.append(locus)

        else:
            missing.append(locus)

    upload = missing + incomplete

    print('Incomplete: {0}'.format(len(upload)))

    return upload


def create_uniprot_queries(file):
    """ Create queries to search for protein annotations
        on UniProt and save the queries in a binary file.

        Args:
            fasta_paths (list): list of paths to fasta files.
        Returns:
            queries_files (list): list of paths to binary files
            with queries to search for protein annotations on
            UniProt.
    """

    # create queries
    # do not create duplicated queries with same protein
    with open(file[1], 'rb') as pb:
        protein_seqs = pickle.load(pb)

    unique_prots = set(list(protein_seqs.values()))

    queries = [aux.uniprot_query(prot) for prot in unique_prots]
    # save in binary file
    queries_file = '{0}_up'.format(file[1])
    with open(queries_file, 'wb') as bup:
        pickle.dump(queries, bup)

    return [file[0], queries_file]


def validate_ptf(configs, input_path):
    """
    """

    ptf_hash = ''
    schema_ptf = ''
    valid = False
    schema_ptfs = configs.get('prodigal_training_file', 'na')
    if len(schema_ptfs) == 1:
        schema_ptf_path = [os.path.join(input_path, file)
                           for file in os.listdir(input_path) if '.trn' in file]
        if len(schema_ptf_path) == 1:
            schema_ptf = schema_ptf_path[0]
            ptf_hash = aux.binary_file_hash(schema_ptf)
            if ptf_hash == schema_ptfs[0]:
                message = 'Found valid training file in schema directory.'
                valid = schema_ptfs[0]
            else:
                message = 'Training file in schema directory is not the original.'
        else:
           message = 'More than one training file in schema directory.'
    elif len(schema_ptfs) > 1 and schema_ptfs != 'na':
        message = 'Schema was used with more than one training file.'
    elif schema_ptfs == 'na':
        message = 'Could not find a valid training file in schema configs.'

    return [valid, message, schema_ptf, ptf_hash]


def validate_bsr(configs):
    """
    """

    valid = False
    schema_bsrs = configs.get('bsr', 'na')
    if len(schema_bsrs) == 1:
        try:
            schema_bsr = float(schema_bsrs[0])
            if schema_bsr > 0.0 and schema_bsr < 1.0:
                message = ('Schema created with BSR value of '
                           '{0}.'.format(schema_bsr))
                valid = str(schema_bsr)
            else:
                raise ValueError('Value is not contained in the '
                                 '[0.0, 1.0] range.')
        except ValueError:
            message = ('Invalid BSR value of {0}. BSR value must be contained'
                       ' in the [0.0, 1.0] interval.'.format(schema_bsrs[0]))
    else:
        message = ('Invalid BSR value or multiple BSR values.')

    return [valid, message]


def validate_msl(configs):
    """
    """

    valid = False
    schema_mls = configs.get('minimum_locus_length', 'na')
    if len(schema_mls) == 1:
        try:
            schema_ml = int(schema_mls[0])
            if schema_ml >= 0:
                message = ('Schema created with a minimum sequence length '
                           'parameter of {0}.'.format(schema_ml))
                valid = str(schema_ml)
            else:
                raise ValueError('Invalid minimum sequence length value. '
                                 'Must be equal or greater than 0.')
        except ValueError:
            message = ('Invalid minimum sequence length value used to '
                       'create schema. Value must be a positive integer.')
    else:
        message = ('Invalid minimum sequence length value.')

    return [valid, message]


def validate_st(configs):
    """
    """

    valid = False
    schema_sts = configs.get('size_threshold', 'na')
    if len(schema_sts) == 1:
        st_value = schema_sts[0]
        try:
            schema_st = float(st_value)
            if schema_st >= 0:
                message = ('Schema created with a size threshold '
                           'parameter of {0}.'.format(schema_st))
                valid = str(schema_st)
            else:
                raise ValueError('Invalid size threshold value. '
                                 'Must be contained in the [0.0, 1.0] interval.')
        except Exception:
            if st_value is None:
                message = ('Schema was created without a size_threshold value.')
                valid = 'None'
            else:
                message = ('Invalid size threshold value used to '
                           'create schema. Value must be None or a '
                           'positive float in the [0.0, 1.0] interval.')
    else:
        message = ('Multiple size threshold values.')

    return [valid, message]


def validate_tt(configs):
    """
    """

    valid = False
    schema_gen_codes = configs.get('translation_table', 'na')
    if len(schema_gen_codes) == 1:
        schema_gen_code = int(schema_gen_codes[0])
        if schema_gen_code in cnst.GENETIC_CODES:
            genetic_code_desc = cnst.GENETIC_CODES[schema_gen_code]
            message = ('Schema genes were predicted with genetic code '
                       '{0} ({1}).'.format(schema_gen_code, genetic_code_desc))
            valid = str(schema_gen_code)
        else:
            message = ('Genetic code used to create schema is not valid.')
    else:
        message = ('Invalid genetic code.')

    return [valid, message]


def validate_cv(configs):
    """
    """

    valid = False
    schema_chewie_versions = configs.get('chewBBACA_version', 'na')
    if len(schema_chewie_versions) == 1:
        chewie_version = cnst.CHEWIE_VERSIONS[cnst.CHEWIE_VERSIONS.index(schema_chewie_versions[0])]
        if chewie_version in cnst.CHEWIE_VERSIONS:
            message = ('Schema created with chewBBACA v{0}.'.format(chewie_version))
            valid = chewie_version
        else:
            message = ('Schema created with chewBBACA version that '
                       'is not suitable to work with the NS.')
    else:
        message = ('Invalid Chewie version.')

    return [valid, message]


def validate_ws(configs):
    """
    """
    
    valid = False
    word_sizes = configs.get('word_size', 'na')
    try:
        if word_sizes == 'na':
            message = ('Schema created with a chewBBACA version '
                       'that did not use clustering.')
            valid = 'None'
        else:
            word_size = int(word_sizes[0])
            if word_size >= 4:
                message = ('Schema created with a clustering word size '
                           'value of {0}.'.format(word_size))
                valid = str(word_size)
            else:
                raise ValueError('Word size for the clustering step '
                                 'must be equal or greater than 4.')
    except ValueError:
        message = ('Schema created with invalid clustering word '
                   'size value.')

    return [valid, message]


def validate_cs(configs):
    """
    """

    valid = False
    cluster_sims = configs.get('cluster_sim', 'na')
    try:
        if cluster_sims == 'na':
            message = ('Schema created with a chewBBACA version '
                       'that did not use clustering.')
            valid = 'None'
        else:
            cluster_sim = float(cluster_sims[0])
            if cluster_sim >= 0.0:
                message = ('Schema created with a clustering threshold '
                           'value of {0}.'.format(cluster_sim))
                valid = str(cluster_sim)
            else:
                raise ValueError('Clustering similarity threshold value '
                                 'must be contained in the [0.0, 1.0] '
                                 'interval.')
    except ValueError:
        message = ('Schema created with invalid clustering '
                   'threshold value.')

    return [valid, message]


def validate_rf(configs):
    """
    """

    valid = False
    representative_filters = configs.get('representative_filter', 'na')
    try:
        if representative_filters == 'na':
            message = ('Schema created with a chewBBACA version '
                       'that did not use clustering.')
            valid = 'None'
        else:
            representative_filter = float(representative_filters[0])
            if representative_filter >= 0.0 and representative_filter <= 1.0:
                message = ('Schema created with a representative filter '
                           'value of {0}.'.format(representative_filter))
                valid = str(representative_filter)
            else:
                raise ValueError('Representative filter threshold value '
                                 'must be contained in the [0.0, 1.0] '
                                 'interval.')
    except ValueError:
        message = ('Schema created with invalid representative filter value.')

    return [valid, message]


def validate_if(configs):
    """
    """

    valid = False
    intraCluster_filters = configs.get('intraCluster_filter', 'na')
    try:
        if intraCluster_filters == 'na':
            message = ('Schema created with a chewBBACA version '
                       'that did not use clustering.')
            valid = 'None'
        else:
            intraCluster_filter = float(intraCluster_filters[0])
            if intraCluster_filter >= 0.0 and intraCluster_filter <= 1.0:
                message = ('Schema created with a intraCluster filter '
                           'value of {0}.'.format(intraCluster_filter))
                valid = str(intraCluster_filter)
            else:
                raise ValueError('intraCluster filter threshold value '
                                 'must be contained in the [0.0, 1.0] '
                                 'interval.')
    except ValueError:
        message = ('Schema created with invalid intraCluster filter '
                   'value.')

    return [valid, message]


def check_schema_status(status_code, species_name):
    """ Checks the schema post status and determines
        if the schema was successfully created in the NS.

        Args:
            status_code (int): schema post status code.
            species_name (str): name of the species.
        Returns:
            message (str): message indicating if the
            schema post was successful or not and why.
    """

    if status_code in [200, 201]:
        message = ('A new schema for {0} was created '
                   'succesfully.'.format(species_name))
    else:
        if status_code == 403:
            message = ('{0}: No permission to load '
                       'schema.'.format(status_code))
        elif status_code == 404:
            message = ('{0}: Cannot upload a schema for a species '
                       'that is not in NS.'.format(status_code))
        elif status_code == 409:
            message = ('{0}: Cannot upload a schema with the same '
                       'description as a schema that is in the '
                       'NS.'.format(status_code))
        else:
            message = '{0}: Could not insert schema.'.format(status_code)

    return message


def post_locus(base_url, headers_post, locus_prefix, keep_file_name, gene,
               uniprot_name, uniprot_label, uniprot_uri):
    """ Adds a new locus to the NS.

        Args:
            base_url (str): the base URI for the NS, used to concatenate
            with a list of elements and obtain endpoints URIs.
            headers_post (dict): headers for the POST method used to
            insert data into the NS.
            locus_prefix (str): prefix for the locus identifier.
            keep_file_name (bool): boolean value indicating if the original
            schema file identifier should be stored in the NS.
            gene (str): identifier of the original schema file.
        Returns:
            loci_url (str): API endpoint for the new locus.
    """

    # Build the url for loci/list
    url_loci = aux.make_url(base_url, 'loci', 'list')

    # Define POST request parameters
    params = {}
    params['prefix'] = locus_prefix
    params['UniprotName'] = uniprot_name
    params['UniprotLabel'] = uniprot_label
    params['UniprotURI'] = uniprot_uri

    if keep_file_name:
        params['locus_ori_name'] = gene

    # Add locus to NS
    res = requests.post(url_loci, data=json.dumps(params),
                        headers=headers_post, timeout=30, verify=False)

    res_status = res.status_code
    if res_status == 409:
        locus_message = '{0}: Locus already exists on NS.'.format(res_status)
    elif res_status == 404:
        locus_message = '{0}: Species not found.'.format(res_status)
    elif res_status == 403:
        locus_message = ('{0}: Unauthorized. No permission to add '
                   'new locus.'.format(res_status))
    elif res_status == 400:
        locus_message = ('{0}: Please provide a valid locus '
                   'prefix.'.format(res_status))

    if 'locus_message' in locals():
        return [False, locus_message]
    else:
        loci_url = res.json()['uri']
        return [True, loci_url]


def get_annotation(sparql_queries):
    """ Queries the UniProt SPARQL endpoint to retrieve
        protein annotations.

        Args:
            sparql_queries (list): a list with a tuple.
            The first element of each tuple is the locus
            identifier and the second element is a list with
            the SPARQL queries and DNA sequences for each
            allele of the locus.
        Returns:
            A tuple with the following elements:
                - locus (str): the locus identifier;
                - prev_name (str): the annotation description.
                - label (str): a descriptive label;
                - url (str): the URL to the UniProt page about
                the protein;
                - dna_seq_to_ns (list): the list of DNA sequences
                that belong to the locus and should be added to the NS.
    """

    locus = sparql_queries[0]
    queries_file = sparql_queries[1]

    # load queries for locus
    with open(queries_file, 'rb') as bup:
        queries = pickle.load(bup)

    virtuoso_server.setReturnFormat(JSON)
    virtuoso_server.setTimeout(10)

    prev_url = ''
    prev_name = ''
    prev_label = ''
    found = False
    unpreferred_names = ['Uncharacterized protein',
                         'hypothetical protein',
                         'DUF',
                         '']

    a = 0
    # define maximum number of tries
    max_tries = 10
    while found is False:

        virtuoso_server.setQuery(queries[a])

        try:
            result = virtuoso_server.query().convert()

            name, url, label = aux.select_name(result)

            if prev_name == '' and name != '':
                prev_name = name
                prev_label = label
                prev_url = url
                if prev_name not in unpreferred_names:
                    found = True

            elif prev_name in unpreferred_names and name not in unpreferred_names:
                prev_name = name
                prev_label = label
                prev_url = url
                found = True

        # retry if the first query failed
        except Exception:
            pass

        a += 1
        if a == max_tries or a == len(queries):
            found = True

    if prev_name == '':
        prev_name = 'not found'
    if prev_label == '':
        prev_label = 'not found'
    if prev_url == '':
        # virtuoso needs a string that looks like an URL
        prev_url = 'http://not.found.org'

    return (locus, prev_name, prev_label, prev_url)


def post_species_loci(url, species_id, locus_id, headers_post):
    """ Adds a new loci to an existing species.

        Args:
            url (str): NS base URI.
            species_id (int): integer identifier for the
            species in the NS.
            locus_id (int): identifier of the locus that
            will be inserted.
            headers_post (dict): headers for the POST method used to
            insert data into the NS.
        Returns:
            True if the POST was successful.
    """

    # Define POST request parameters
    params = {}
    params['locus_id'] = locus_id

    # Build the url for the loci of the new schema
    url_species_loci = aux.make_url(url, 'species', species_id, 'loci')

    # insert new locus
    res = requests.post(url_species_loci, data=json.dumps(params),
                        headers=headers_post, timeout=30, verify=False)

    if res.status_code > 201:
        message = ('{0}: Failed to link locus to '
                   'species.'.format(res.status_code))

    if 'message' in locals():
        return [False, message]
    else:
        species_loci_url = '{0}/{1}'.format(url_species_loci, locus_id)
        return [True, species_loci_url]


def post_schema_loci(loci_url, schema_url, headers_post):
    """ Adds a new loci to an existing schema.

        Args:
            loci_url (str): URI for the loci that will be added.
            schema_url (str): URI for the schema that we want to
            add the locus to.
            headers_post (dict): headers for the POST method used to
            insert data into the NS.
        Returns:
            True if the POST was successful.
    """

    # Get the new loci id from the new loci url
    new_loci_id = str(int(loci_url.split('/')[-1]))

    # Define POST request parameters
    params = {}
    params['loci_id'] = new_loci_id

    # Build the url for the loci of the new schema
    url_schema_loci = aux.make_url(schema_url, 'loci')

    res = requests.post(url_schema_loci, data=json.dumps(params),
                        headers=headers_post, timeout=30, verify=False)

    if res.status_code > 201:
        message = ('{0}: Failed to link locus to '
                   'schema.'.format(res.status_code))

    if 'message' in locals():
        return [False, message]
    else:
        schema_loci_url = '{0}/{1}'.format(url_schema_loci, new_loci_id)
        return [True, schema_loci_url]


def quality_control(inputs):
    """
    """

    size_threshold = float(inputs[4]) if inputs[4] is not None else None
    res = aux.get_seqs_dicts(inputs[0], inputs[1], int(inputs[2]), int(inputs[3]), size_threshold)

    prots_file = '{0}_prots'.format(inputs[0].split('.fasta')[0])
    with open(prots_file, 'wb') as pb:
        pickle.dump(res[1], pb)

    if len(res[2]) > 0:
        print('Found {0} invalid alleles for locus {1}.'.format(len(res[2]), inputs[1]))

    return [inputs[0], prots_file, res[2]]


def parse_arguments():

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('-i', type=str, required=True,
                        dest='schema_directory',
                        help='Path to the directory with the local schema '
                             'files.')

    parser.add_argument('-sp', type=str, required=True,
                        dest='species_id',
                        help='The integer identifier or name of the species '
                             'that the schema will be associated to in '
                             'the NS.')

    parser.add_argument('-sd', type=str, required=True,
                        dest='schema_description',
                        help='A brief and meaningful description that '
                             'should help understand the type and content '
                             'of the schema.')

    parser.add_argument('-lp', type=str, required=True,
                        dest='loci_prefix',
                        help='Prefix included in the name of each locus of '
                             'the schema.')

    parser.add_argument('--cpu', type=int, required=False,
                        dest='cpu_cores', default=1,
                        help='Number of CPU cores that will '
                             'be used in multiprocessing steps.')

    parser.add_argument('--thr', type=int, required=False,
                        default=20, dest='threads',
                        help='Number of threads to use to upload the alleles '
                             'of the schema.')

    parser.add_argument('--ns_url', type=str, required=False,
                        default=cnts.HOST_NS,
                        dest='nomenclature_server_url',
                        help='The base URL for the Nomenclature Server.')

    parser.add_argument('--continue_up', required=False, action='store_true',
                        dest='continue_up',
                        help='If the process should check if the schema '
                             'upload was interrupted and try to finish it.')

    args = parser.parse_args()

    schema_directory = args.schema_directory
    species_id = args.species_id
    schema_description = args.schema_description
    loci_prefix = args.loci_prefix
    cpu_cores = args.cpu_cores
    threads = args.threads
    nomenclature_server_url = args.nomenclature_server_url
    continue_up = args.continue_up

    return [schema_directory, species_id, schema_description,
            loci_prefix, cpu_cores, threads,
            nomenclature_server_url, continue_up]


#input_files = '/home/rfm/Desktop/ns_test/test_ns_createschema/'
#species_id = 1
#schema_desc = 'sagalactiae3'
#loci_prefix = 'sagalactiae3'
#cpu_cores = 6
#threads = 10
#base_url = 'http://127.0.0.1:5000/NS/api/'
#continue_up = False

def main(input_files, species_id, schema_desc, loci_prefix, cpu_cores,
         threads, base_url, continue_up):

    # login with master key
    login_key = False
    if login_key:
        pass
    # if the login key is not found ask for credentials
    else:
        print('\nCould not find private key.')
        print('\nPlease provide login credentials:')
        user = input('USERNAME: ')
        password = getpass('PASSWORD: ')
        print()
        # get token
        token = aux.login_user_to_NS(base_url, user, password)
        # if login was not successful, stop the program
        if token is False:
            message = '403: Invalid credentials.'
            sys.exit(message)

    total_start = time.time()

    # Define the headers of the requests
    headers_get = {'Authorization': token,
                   'accept': 'application/json'}

    # verify user role to check permission
    user_info = aux.simple_get_request(base_url, headers_get,
                                       ['user', 'current_user'])
    user_info = user_info.json()
    user_role = any(role in user_info['roles']
                    for role in ['Admin', 'Contributor'])

    if not user_role:
        sys.exit('\nCurrent user has no Administrator '
                 'or Contributor permissions.\n'
                 'Not allowed to upload schemas.')

    user_id = str(user_info['id'])
    headers_post = {'Authorization': token,
                    'Content-type': 'application/json',
                    'accept': 'application/json',
                    'user_id': user_id}

    # check if there is config file and load it
    config_file = os.path.join(input_files, '.schema_config')
    if os.path.isfile(config_file):
        print('Found config file. Loading configs...')
        # Load configs dictionary
        with open(config_file, 'rb') as cf:
            configs = pickle.load(cf)
    else:
        sys.exit('Could not find a valid config file. Cannot upload'
                 ' schema without checking for valid parameters values.')

    # validate arguments values
    ptf_val = validate_ptf(configs, input_files)
    bsr_val = validate_bsr(configs)
    msl_val = validate_msl(configs)
    tt_val = validate_tt(configs)
    st_val = validate_st(configs)
    cv_val = validate_cv(configs)
    ws_val = validate_ws(configs)
    cs_val = validate_cs(configs)
    rf_val = validate_rf(configs)
    if_val = validate_if(configs)

    valid_list = [ptf_val[0], bsr_val[0], msl_val[0], tt_val[0], st_val[0],
                  cv_val[0], ws_val[0], cs_val[0], rf_val[0], if_val[0]]

    messages_list = [ptf_val[1], bsr_val[1], msl_val[1], tt_val[1],
                     st_val[1], cv_val[1], ws_val[1], cs_val[1],
                     rf_val[1], if_val[1]]

    for m in messages_list:
        print(m)

    if all(valid_list) is not True:
        sys.exit('Found invalid parameters values and exited.')
    else:
        print('All configurations successfully validated.')
        params = {'bsr': bsr_val[0], 'prodigal_training_file': ptf_val[0],
                  'translation_table': tt_val[0], 'minimum_locus_length': msl_val[0],
                  'chewBBACA_version': cv_val[0], 'size_threshold': st_val[0],
                  'word_size': ws_val[0], 'cluster_sim': cs_val[0],
                  'representative_filter': rf_val[0], 'intraCluster_filter': if_val[0]}
        params['name'] = schema_desc
        ptf_file = ptf_val[2]
        ptf_hash = ptf_val[3]

    # Check if user provided a list of genes or a folder
    fasta_paths = [os.path.join(input_files, file)
                   for file in os.listdir(input_files) if '.fasta' in file]
    fasta_paths.sort()

    # Get the name of the species from the provided id
    # or vice-versa
    species_info = aux.species_ids(species_id, base_url, headers_get)
    if isinstance(species_info, list):
        species_id, species_name = species_info
        print('\nNS species with identifier {0} is {1}.'.format(species_id,
                                                                species_name))
    else:
        sys.exit('\nThere is no species with the provided identifier in the NS.')

    # check if schema already exists
    schema_get = aux.simple_get_request(base_url, headers_get,
                                        ['species', species_id, 'schemas'])
    schema_get_status = schema_get.status_code
    species_schemas = schema_get.json()
    if schema_get_status in [200, 201]:
        # determine if there is a schema for current
        # species with same description
        schema_info = retrieve_schema_info(species_schemas, schema_desc)

        if isinstance(schema_info, int):
            if continue_up is False:
                print('Will create a new schema with description {0}.'.format(schema_desc))
            elif continue_up is True: 
                sys.exit('\nCannot continue uploading to a schema that '
                         'does not exist.')
        else:
            if continue_up is False:
                sys.exit('A schema with provided description already exists.')
            elif continue_up is True:
                print('Schema exists. Checking if it was not fully uploaded...')
                schema_url, schema_id = schema_info
                schema_get = aux.simple_get_request(base_url, headers_get,
                                        ['species', species_id, 'schemas', schema_id])
                current_schema = schema_get.json()[0]
                schema_date = current_schema['dateEntered']['value']
                if schema_date != 'singularity':
                    sys.exit('Schema finished uploading. Cannot proceed.')
                # determine if user was the one that started the upload
                ask_admin = aux.simple_get_request(base_url, headers_get,
                                        ['species', species_id, 'schemas', schema_id, 'administrated'])
                schema_admin = ask_admin.json()
                if schema_admin is False:
                    sys.exit('Current user is not the user that started schema upload.')
    elif 'NOT FOUND' in species_schemas:
        print('Species still has no schemas.')
    else:
        print('\nCould not retrieve schemas for current species.')
        sys.exit(1)

    # start validating and processing schema files
    # translate alleles and save results
    inputs = [(file,
               file.split('/')[-1].split('.fasta')[0],
               params['translation_table'],
               params['minimum_locus_length'],
               None) for file in fasta_paths]

    start = time.time()
    qc_results = []
    genes_pools = multiprocessing.Pool(processes=cpu_cores)
    rawr = genes_pools.map_async(quality_control, inputs,
                                 callback=qc_results.extend)
    rawr.wait()
    end = time.time()
    delta = end - start
    print(delta/60)

    invalid_alleles = [r[2] for r in qc_results]
    invalid_alleles = list(itertools.chain.from_iterable(invalid_alleles))
    invalid_identifiers = set([r[0] for r in invalid_alleles])

    loci_files = [r[:2] for r in qc_results]

    print('\nCreating queries to search UniProt for annotations...')
    start = time.time()
    queries_files = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        for res in executor.map(create_uniprot_queries, loci_files):
            queries_files.append(res)
    end = time.time()
    delta = end - start
    print(delta/60)
    print('Done.')

    # find annotations for all loci with multithreading
    print('\nSearching for annotations on UniProt...')
    loci_annotations = []
    total_found = 0
    total_loci = len(queries_files)
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        # Start the load operations and mark each future with its URL
        for res in executor.map(get_annotation, queries_files):
            loci_annotations.append(res)
            total_found += 1
            print('\r', 'Found annotations for '
                  '{0}/{1} loci.'.format(total_found, total_loci), end='')

    # determine lengths of all alleles per locus
    length_files = []
    for l in loci_annotations:
        file = l[0]
        locus = os.path.basename(file)
        locus_lengths = {locus: {hashlib.sha256(str(rec.seq).encode('utf-8')).hexdigest(): len(rec.seq) for rec in SeqIO.parse(file, 'fasta')}}
        lengths_file = os.path.join(input_files, '{0}_lengths'.format(locus.split('.fasta')[0]))
        with open(lengths_file, 'wb') as lf:
            pickle.dump(locus_lengths, lf)
        length_files.append(lengths_file)

    # start sending data
    print('\n\nSending data to the NS...')
    
    params['SchemaDescription'] = 'This is a very long description that I could not make any longer.'
    # Build the new schema URL and POST to NS
    if continue_up is False:
        print('\nCreating new schema...')
        # schema is created in locked state
        schema_post = aux.simple_post_request(base_url, headers_post,
                                              ['species', species_id, 'schemas'],
                                              params)
        schema_status = schema_post.status_code
    elif continue_up is True:
        print('Determining loci absent from NS schema...')
        # compare list of genes, if they do not intersect, halt process
        # get list of loci for schema in NS
        ns_loci_get = aux.simple_get_request(base_url, headers_get,
                                             ['species', species_id,
                                              'schemas', schema_id,
                                              'loci'])
        # get loci files names from response
        ns_schema_loci = []
        ns_schema_locid_map = {}
        if 'message' not in ns_loci_get.json():
            for l in ns_loci_get.json()['Loci']:
                locus_file = l['original_name']['value']
                ns_schema_loci.append(locus_file)
                locus_uri = l['locus']['value']
                locus_name = l['name']['value']
                ns_schema_locid_map[locus_file] = (locus_uri, locus_name)

        # get list of loci for schema to upload
        local_schema_loci = [l[0].split('/')[-1] for l in loci_annotations]

        local_loci_set = set(local_schema_loci)
        ns_loci_set = set(ns_schema_loci)

        # verify that the number of loci in NS is not greater than
        # in the local schema
        ns_loci_diff = ns_loci_set - local_loci_set
        if len(ns_loci_diff) > 0:
            sys.exit('NS schema has loci that are not in the local schema.')
        if len(ns_loci_set) > len(local_loci_set):
            sys.exit('NS schema has more loci than the local schema.')
        elif len(ns_loci_set) < len(local_loci_set):
            print('NS schema has less loci than local schema.')
            absent_loci = list(local_loci_set-ns_loci_set)
            absent_text = [absent_loci[i:i+4] for i in range(0, len(absent_loci), 4)]
            absent_text = [('{:30} '*len(g)).format(*g) for g in absent_text]
            absent_text = '\n'.join(absent_text)
            print('Absent loci: \n{0}'.format(absent_text))
        elif len(ns_loci_set) == len(local_loci_set):
            print('NS and local schemas have the same number of loci.')

        # if the set of loci is equal, check sequences in each locus
        # if a locus in the NS has more sequences than one in the local
        # set, halt process
        upload = determine_upload(local_schema_loci, ns_schema_loci,
                                  ns_schema_locid_map, input_files,
                                  base_url, headers_get)

        if isinstance(upload, tuple):
            sys.exit(upload[0])
        elif len(upload) == 0:
            sys.exit('Local and NS schemas are identical. Nothing left to do.')

        loci_annotations = [list(res) for res in loci_annotations if
                            any(locus in res[0] for locus in upload)]

        for r in range(len(loci_annotations)):
            lfile = (loci_annotations[r][0]).split('/')[-1]
            if lfile in ns_schema_locid_map:
                loci_annotations[r].append(ns_schema_locid_map[lfile][0])

    # check status code
    # add other prints for cases that fail so that users see a print explaining
    if continue_up is False:
        schema_insert = check_schema_status(schema_status,
                                            species_name)
        print(schema_insert)
        if schema_status not in [200, 201]:
            sys.exit(1)

        schema_url = schema_post.json()['url']
        schema_id = schema_url.split('/')[-1]

    # Get the new schema url from the response
    print('Schema description: {0}'.format(schema_desc))
    print('Schema URI: {0}\n'.format(schema_url))

    # start creating new loci and adding/linking alleles
    print('Creating loci and adding/linking alleles...\n')
    start = time.time()
    # create new loci, link them to species and to new schema
    ns_ids = {}
    loci_urls = {}
    inserted_loci = 0
    schema_linked = 0
    species_linked = 0
    total_loci = len(loci_annotations)
    for locus in loci_annotations:
        locus_file = locus[0]
        locus_basename = os.path.basename(locus_file)

        uniprot_name = locus[1]
        uniprot_label = locus[2]
        uniprot_uri = locus[3]
        # still need to add user annotation and custom annotation

        if len(locus) == 5:
            # re-upload alleles to existing locus
            new_loci_url = locus[4]
            new_loci_id = new_loci_url.split('/')[-1]
            print('Re-uploading locus: {0}'.format(new_loci_url))

        else:
            # Create a new locus
            new_loci_status, new_loci_url = post_locus(base_url, headers_post,
                                                       loci_prefix, True,
                                                       locus_basename,
                                                       uniprot_name,
                                                       uniprot_label,
                                                       uniprot_uri)
            if new_loci_status is False:
                print('{0}'.format(new_loci_url))
                continue
            elif new_loci_status is True:
                print('Created new locus: {0}'.format(new_loci_url))
                inserted_loci += 1

            # Get the new loci ID
            new_loci_id = new_loci_url.split('/')[-1]

            # Associate the new loci id to the species
            species_link_status, species_link_url = post_species_loci(base_url,
                                                                      species_id,
                                                                      new_loci_id,
                                                                      headers_post)
            if species_link_status is False:
                print('{0}'.format(species_link_url))
                continue
            elif species_link_status is True:
                print('Linked new locus to species: '
                      '{0}'.format(species_link_url))
                species_linked += 1

            # Associate the new loci id to the new schema
            schema_loci_status, schema_link_url = post_schema_loci(new_loci_url,
                                                                   schema_url,
                                                                   headers_post)
            if schema_loci_status is False:
                print('{0}'.format(schema_link_url))
                continue
            elif schema_loci_status is True:
                print('Linked new locus to schema: '
                      '{0}'.format(schema_link_url))
                schema_linked += 1

        loci_urls[locus_file] = new_loci_url
        ns_id = '{0}-{1}'.format(loci_prefix, '%06d' % (int(new_loci_id),))
        ns_ids[locus_basename.split('.fasta')[0]] = ns_id

    print('Inserted {0}/{1} new loci.'.format(inserted_loci, total_loci))
    print('Linked {0}/{1} new loci to species "{2}"'.format(species_linked, total_loci, species_name))
    print('Linked {0}/{1} new loci to schema "{2}"'.format(species_linked, total_loci, schema_desc))

    end = time.time()
    delta = end - start
    print(delta/60)

################

    # create files with info for posting alleles
    post_files = []
    for locus in loci_annotations:
        locus_file = locus[0]
        locus_basename = locus_file.split('/')[-1]
        allele_seq_list = [str(rec.seq)
                           for rec in SeqIO.parse(locus_file, 'fasta') if rec.id not in invalid_identifiers]
        post_inputs = aux.create_allele_data(allele_seq_list, loci_urls[locus_file],
                                             species_name, base_url, user_id, 1)

        locus_id = loci_urls[locus_file].split('/')[-1]
        alleles_file = os.path.join(input_files, '{0}_{1}_{2}'.format(species_id, schema_id, locus_id))
        with open(alleles_file, 'wb') as af:
            pickle.dump(post_inputs, af)

        post_files.append(alleles_file)

    # zip all files
    zipped_files = []
    for file in post_files:
        zip_file = '{0}.zip'.format(file)
        with zipfile.ZipFile(zip_file, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(file, os.path.basename(file))
        zipped_files.append(zip_file)

    # send POST with file contents and process each file in the NS
    uploaded = 0
    for file in zipped_files:
        with open(file, 'rb') as p:
            zip_content = p.read()
            zip_content = zip_content.decode(encoding='ISO-8859-1')

            locus_id = file.split('_')[-1].split('.zip')[0]
            zip_url = '{0}species/{1}/schemas/{2}/loci/{3}/data'.format(base_url, species_id, schema_id, locus_id)

            response = requests.post(zip_url,
                                     headers=headers_post,
                                     data=json.dumps({'filename': os.path.basename(file), 'content': zip_content}),
                                     verify=False)
            uploaded += 1
            print('\r', uploaded, end='')

    # send files with alleles length values
    uploaded = 0
    for file in length_files:
        with open(file, 'rb')as f:
            data = pickle.load(f)
        
        file_basename = file.split('/')[-1]
        locus = ns_ids[file_basename.split('_lengths')[0]]
        locus_id = locus.split('-')[-1].lstrip('0')
        data = {locus_id: data[list(data.keys())[0]]}
        send_url = '{0}species/{1}/schemas/{2}/loci/{3}/lengths'.format(base_url, species_id, schema_id, locus_id)

        response = requests.post(send_url,
                                 headers=headers_post,
                                 data=json.dumps({'content': data}),
                                 verify=False)
        uploaded += 1
        print('\r', uploaded, end='')


    # send training file to sftp folder
    print('\nUploading Prodigal training file...')

    with open(ptf_file, 'rb') as p:
        ptf_content = p.read()
        # json.dumps cannot serialize objects of type bytes
        # training file contents cannot be decoded with 'utf-8'
        # decoding is successful with ISO-8859-1
        ptf_content = ptf_content.decode(encoding='ISO-8859-1')

    ptf_url = '{0}species/{1}/schemas/{2}/ptf'.format(base_url, species_id, schema_id)

    response = requests.post(ptf_url,
                             headers=headers_post,
                             data=json.dumps({'filename': ptf_hash, 'content': ptf_content}),
                             verify=False)
    print(list(response.json().values())[0])

    # delete all intermediate files
    # for i in range(len(queries_files)):
    #     os.remove(loci_files[i][1])
    #     os.remove(queries_files[i][1])
    #     os.remove(length_files[i])
    #     os.remove(post_files[i])
    #     os.remove(zipped_files[i])

    total_end = time.time()
    total_delta = total_end - total_start

    # determine elapsed time in minutes
    minutes = int(total_delta / 60)
    seconds = int(total_delta % 60)
    print('\nElapsed time: {0}m{1}s'.format(minutes, seconds))


if __name__ == "__main__":

    args = parse_arguments()
    main(args[0], args[1], args[2], args[3],
    	 args[4], args[5], args[6], args[7])
