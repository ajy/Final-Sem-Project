#!/usr/bin/python
import re
import dateutil.parser
from datetime import datetime, timedelta
from pymongo import MongoClient
from pandas import DataFrame
from referer_parser import Referer
from ua_parser import user_agent_parser
from numpy import asscalar
from urlparse import urlparse
import pygeoip
import os

if os.environ.get('MONGOLAB_URI'):
    default_db = 'final'
else:
    default_db = 'test'

class LogParser:
    def __init__(self, db = default_db):
        mongolab_uri = os.environ.get('MONGOLAB_URI')
        if mongolab_uri:
            self.client = MongoClient(host = mongolab_uri)
        else:
            self.client = MongoClient()
        self.db = self.client[db]
        self.geoip = pygeoip.GeoIP('./GeoIP.dat', pygeoip.MEMORY_CACHE) # change the path on your machine. get it from http://dev.maxmind.com/geoip/install/country
    
    def log_insert(self, collection_name, data):
      collection = self.db[collection_name]
      collection.insert(data)

    def load_apache_log_file_into_DB(self, file_name, collection_name):
        """
        Reads the log data from 'file_name' and loads it into 'collection_name' in MongoDB
        """
        fields = ['client_ip','date','request','status','request_size','browser_string']
        apache_log_regex = '([\da-zA-Z:\.\-]+) - [^ ]+ \[(.*?)\] "(.*?)" (\d+) ((\d+)|-) ("-")?(.*?) "(.*?)"'
        compiled_apache_log_regex = re.compile(apache_log_regex)
        log_file = open(file_name,"r")
        count = 0
        log_list = []
        for line in log_file.readlines():
            # try:
                search = compiled_apache_log_regex.match(line).groups()
                date = dateutil.parser.parse(search[1].replace(':', ' ', 1))
                date = (datetime(date.year,date.month,date.day,date.hour,date.minute,date.second),)
                if len(search[2].split()) > 1:
                    path = (search[2].split()[1],)
                else:
                    path = ('-',)
                log_data = dict(zip(fields,search[:1]+date+path+search[3:5]+search[8:]))
                log_data['base_url'] = urlparse(log_data['request']).geturl()
                if(log_data['request_size'] == '-'):
                    log_data['request_size'] = 0
                else:
                    log_data['request_size'] = int(log_data['request_size'])
                log_data.update(self.extract_user_agent_info(log_data['browser_string']))
                request_country = self.geoip.country_name_by_addr(search[0])
                log_data['request_country'] = request_country
                count += 1
                log_list.append(log_data)
                if count == 400:
                    count = 0
                    self.log_insert(collection_name,log_list)
                    log_list = []
            # except :
                # print line

        if collection_name not in self.db.collection_names():
            return False
        if len(log_list) > 0:
            self.log_insert(collection_name,log_list)
        return True

    def extract_user_agent_info(self, ua_string):
        result_dict = user_agent_parser.Parse(ua_string)
        referer_url = re.search("(?P<url>https?://[^\s]+)", ua_string)
        ua_info_dict = {'device' : result_dict['device']['family'],'os' : result_dict['os']['family'], 'browser' : result_dict['user_agent']['family'], 'browser_version' : result_dict['user_agent']['major']}
        if referer_url is not None:
            ua_info_dict['referer'] = Referer(referer_url.group("url")).uri[1]
        else:
            ua_info_dict['referer'] = 'None'
        return ua_info_dict

class CollectionNotFound(Exception):
    pass

class LogAnalyzer:    
    def __init__(self, db = default_db, collection = 'None', from_date = None, to_date = None):
        mongolab_uri = os.environ.get('MONGOLAB_URI')
        if mongolab_uri:
            self.client = MongoClient(host = mongolab_uri)
        else:
            self.client = MongoClient()
        self.db = self.client[db]
        self.from_date = from_date
        self.to_date = to_date

        if collection not in self.db.collection_names():
            raise CollectionNotFound("The given collection was not found in the DB.")
        self.collection = self.db[collection]

        if from_date is None:
            self.from_date = datetime(1970,1,1)
        if to_date is None:
            self.to_date = datetime.now()
        self.log_data = self.collection.find({'date' : {"$gte": self.from_date, "$lt" : self.to_date}})

        self.log_fields = ['client_ip','date','request','status','request_size','browser_string','device' ,'os','browser','browser_version' ,'referer', 'request_country']

    def load_apache_logs_into_DataFrame(self):
        # log_data = self.collection.find()
        return DataFrame(list(self.log_data),columns = self.log_fields, dtype=float)

    def get_log_data(self, page_number = 0):
        page_size = 50
        page_count = self.log_data.count()/page_size
        paginated_log_data = self.log_data.skip(page_number * page_size).limit(page_size)
        log_list = []
        for log in paginated_log_data:
            log_list.append(log)
            log['date'] = log['date'].strftime("%s")
            log.pop('_id')
        return {"data" : log_list, "max_page" : page_count}

    def get_log_date_range(self):
        min = self.collection.find().sort([("date", 1)]).limit(1)
        max = self.collection.find().sort([("date", -1)]).limit(1)
        return {"min_date" : min[0]['date'].strftime("%s"),"max_date" : max[0]['date'].strftime("%s")}


    def median(self, df, mean_of, group_by = None):
        computed_mean = None
        try:
            if(group_by):
                computed_mean = df.groupby(group_by)[mean_of].median()
            else:   
                computed_mean = df[mean_of].median()
        except KeyError:
            print 'Check if the field ' + mean_of + ' exists.'
        return computed_mean

    def count(self, df, field):
        return df[field].count()
    
    def group_by(self, df, field):
        if field == 'date':
            return df.groupby([df['date'].map(lambda x: (x.year, x.month, x.day)),df['status']])
        return df.groupby(field)
    
    def sum(self, df, field):
        return df[field].sum()
    
    def generate_stats(self):
        stats_dict = {}
        stats_dict['date_range'] = self.get_log_date_range()

    def to_dict(self, data, key_label = 'label', value_label = 'value'):
        '''
        Function to convert pandas datetypes to jsonable dicts
        '''
        class_name = data.__class__.__name__
        if class_name == 'Series':
            data_list = []
            for row in data.index:
                data_list.append({\
                        key_label : row\
                        , value_label : asscalar( data[row] )\
                        })
            return {"data" : data_list}
        elif class_name == 'DataFrame':
            data_list = []
            for row in data.value_labels:
                row_list = []
                for i,colname in enumerate(data.columns):
                    if row[i].__class__.__name__ == "datetime":
                        data = row[i].strftime("%s")
                    else:
                        data = row[i]
                    row_list.append((colname, data))
                data_list.append(dict(row_list))
            return {"data" : data_list}
        else:
            raise TypeError('Unexpected type '+ class_name +', could not be handled by this function')

    def daily_bandwidth_sums(self):
        min_date = self.collection.find().sort([("date", 1)]).limit(1)[0]['date']
        max_date = self.collection.find().sort([("date", -1)]).limit(1)[0]['date']
        current = min_date
        date_data = []
        while current < max_date:
            self.log_data = self.collection.find({'date' : {"$gte": current, "$lt" : current + timedelta(1)}})
            data = self.load_apache_logs_into_DataFrame()
            date_data.append({'date' : current.strftime("%s"), 'bandwidth' : self.sum(data, 'request_size')})
            current = current + timedelta(1)
        return {'data' : date_data}
    def count_hits(self, collection_name):
        """Not needed anymore, just a function to do all the processing in one go for results"""
        df = self.load_apache_logs_into_DataFrame(collection_name)
        if df is False:
            return False
        df = self.group_by(df, 'date')
        count_data = self.count(df, 'request_size')
        counts_list = []
        for row in count_data.index:
            counts_list.append({"date" : datetime(row[0][0],row[0][1],row[0][2]).strftime("%s"), "status" : row[1], "count" : str(count_data[row])})
        return {"data" : counts_list}

