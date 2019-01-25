#from pyarrow import RecordBatchStreamReader
import pandas as pd
import pyarrow as pa
from pymapd import connect
import json
import os
import numpy as np
import time
import sys


class omnisci_utils:
	#pandas_df = None
	#back_up_dimension_pandas = None
	dimensions_filters = {}
	group_by_backups = {}

	def __init__(self):
		#self.pandas_df = None
		#self.back_up_dimension_pandas = None
		self.dimensions_filters = {}
		self.dimensions_filters_response_format = {}
		self.group_by_backups = {}
		self.connection = None # new
		self.table = None # new


	def hist_numpy(self,dimension_name, bins, query = ""):
		'''
			description:
				Calculate histogram leveraging gpu via pycuda(using numba jit)
			input:
				data: np array, bins: number of bins in the histogram
			Output:
				json -> {X:[__values_of_colName_with_max_64_bins__], Y:[__frequencies_per_bin__]}
				
			SELECT 
			ceiling(borrower_credit_score / (select cast(((max(borrower_credit_score)-min(borrower_credit_score)) / 6) as float) FROM Mortgages_146M_predictions_v2)) *
			(select ((max(borrower_credit_score)-min(borrower_credit_score)) / 6) FROM Mortgages_146M_predictions_v2)
			,count()
			FROM Mortgages_146M_predictions_v2 
			group by 1
			order by 1;
		
		'''
		
		part = "(select cast(((max(%s)-min(%s)) / %s) as float) FROM %s)" % (dimension_name, dimension_name, str(bins), self.table)
		if (len(query)>0):
			query = "select ceiling(%s / %s) * %s, count() as freq from %s where %s group by 1 order by 1;" % (dimension_name, part, part, self.table, query)
		else:
			query = "select ceiling(%s / %s) * %s, count() as freq from %s group by 1 order by 1;" % (dimension_name, part, part, self.table)
		
		query = query.replace("==", "=")		
		print("******* %s *********" % query)
 		
		with self.connection as c:
			c.execute(query)
			result = list(c)
		df1 = np.array(result).T
		dict_temp ={}

		dict_temp['X'] = list(df1[0].astype(float))
		dict_temp['Y'] = list(df1[1].astype(float))

		return json.dumps(dict_temp)

	def groupby(self, query_where, column_name, groupby_agg, groupby_agg_key, sort_order, num_rows, sort_column):
		'''
			description:
				Calculate groupby on a given column on the pygdf
			input:
				data: pygdf row as a series -> gpu mem pointer,
				column_name: column name
				sort_order, num_rows, sort_column
			Output:
				json -> {A:[__values_of_colName_with_max_64_bins__], B:[__frequencies_per_bin__]}
		'''

		try:
			#group_appl = data.groupby(by=[column_name]).agg(groupby_agg)
			#group_appl.reset_index(inplace=True)
			
			query = "select " + column_name
			for k in list(groupby_agg.keys()):
				agg = groupby_agg[k][0]
				if (agg == "mean"):
					agg = "avg"
				query += ",%s(%s) as %s" % (agg, k, k)
			query += " from " + self.table
			if (len(query_where)>0):
				query += " where " + query_where
			query += " group by 1 order by 1 "
			if (sort_order == "bottom"):
				query += " desc "
			if (num_rows != None and sort_order != "all"):
				query += " limit " + str(num_rows)
			query += ";"
			
			query = query.replace("==", "=")
			print("******* %s *********" % query)

			with self.connection as c:
				c.execute(query)
				result = list(c)
				
			temp_dict = {}
			data = np.array(result)
			temp_dict[column_name] = list(data[:,0])
			
			i = 1
			for k in list(groupby_agg.keys()):
				temp_key = str(groupby_agg[k][0]) + "_" + str(k)
				temp_dict[temp_key] = list(data[:,i])
				i += 1

			key = column_name+"_"+groupby_agg_key
			self.group_by_backups[key] = temp_dict
			
		except Exception as e:
			return "Exception *** in omnisci groupby():"+str(e)

		return temp_dict

	def get_columns(self):
		'''
			description:
				Column names in a data frame
			Output:
				list of column names as string
		'''
		try:
			response = str(self._get_columns())
		except Exception as e:
			response = "Exception *** in omnisci get_columns():"+str(e)
		return response

	def read_data(self,load_type,file):
		'''
			description:
				Initialize connection to database
			input:
				load_type: csv or arrow
				file: table name
			return:
				status
		'''
		status = self.init_connection(uri = "mapd://mapd:HyperInteractive@localhost:9091/mapd?protocol=binary", table = file)
		return status
	
	def init_connection(self, uri, table):
		self.table = table
		self.connection = connect(uri=uri)
		tables = self.connection.get_tables()
		if (table in tables):
			response = "data read successfully"
		else:
			response = "Exception *** in omnisci, table not found: " + str(table)
		return response

	def default(self,o):
		if isinstance(o, np.int8) or isinstance(o, np.int16) or isinstance(o, np.int32) or isinstance(o, np.int64): return int(o)
		elif isinstance(o, np.float16) or isinstance(o, np.float32) or isinstance(o, np.float64): return float(o)
		raise TypeError
		
	"""
	def parse_dict(self,data):
		'''
			description:
				get parsed string format of the dictionary, that can be sent to the socket-client
			input:
				data: dataframe
			return:
				shape string
		'''
		try:
			temp_dict = {}
			for i in data:
				if i[1] == '':
					temp_key = i[0]
				else:
					temp_key = '_'.join(i[::-1])
				temp_dict[temp_key] = list(data[i].values())
			return json.dumps(temp_dict,default=self.default)
		except Exception as e:
			return "Exception *** in pandas parse_dict() (helper function):"+str(e)
	"""
	
	def get_size(self):
		'''
			description:
				get shape of the dataframe
			return:
				shape tuple
		'''
		try:
			return str(self._get_filtered_size())
		except Exception as e:
			return "Exception *** in pandas get_size():"+str(e)

	def reset_filters(self, omit=None, include_dim=['all']):
		'''
			description:
				reset filters on the data_gpu dataframe by executing all filters in the dimensions_filters dictionary
			input:
				omit: column name, the filters associated to which, are to be omitted
				include_dim: list of column_names, which are to be included along with dimensions_filters.keys(); ['all'] to include all columns

			Output:
				result dataframe after executing the filters using the dataframe.query() command
		'''
		try:
			temp_list = []
			for key in self.dimensions_filters.keys():
				if omit is not None and omit == key:
					continue
				if len(self.dimensions_filters[key])>0:
					temp_list.append(self.dimensions_filters[key])
			query = ' and '.join(temp_list)
			if(len(query) >0):
				# return data.query(query)
				if include_dim[0] == 'all':
					#return data.query(query)
					return query
				else:
					column_list = list(set(list(self.dimensions_filters.keys())+include_dim))
					try:
						#return_val = data.loc[:,column_list].query(query)
						return_val = query
					except Exception as e:
						return 'Exception *** in pandas reset_filters():'+str(e)
					return return_val
			else:
				#return data
				return ''
		except Exception as e:
			return "Exception *** in pandas reset_filters():"+str(e)

	def reset_all_filters(self):
		'''
			description:
				reset all filters on all dimensions for the dataset
			input:
				None
			Output:
				number_of_rows_left
		'''
		try:
			self.dimensions_filters.clear()
			self.dimensions_filters_response_format.clear()
			return self.get_size()
		except Exception as e:
			return "Exception *** in pandas reset_all_filters():"+str(e)

	def dimension_load(self, dimension_name):
		'''
			description:
				load a dimension
			Get parameters:
				dimension_name (string)
			Response:
				status -> success: dimension loaded successfully/dimension already exists   // error: "dimension not initialized"
		'''
		try:
			if dimension_name not in self.dimensions_filters:
				self.dimensions_filters[dimension_name] = ''
				self.dimensions_filters_response_format[dimension_name] = []
				res = 'dimension loaded successfully'
			else:
				res = 'dimension already exists'
			return res
		except Exception as e:
			return "Exception *** in pandas dimension_load():"+str(e)

	def dimension_reset(self, dimension_name):
		'''
			description:
				reset all filters on a dimension for pandas df
			Get parameters:
				dimension_name (string)
			Response:
				number_of_rows
		'''
		try:
			self.dimensions_filters[dimension_name] = ''
			self.dimensions_filters_response_format[dimension_name] = []

			return self._get_filtered_size(self.reset_filters());

		except Exception as e:
			return "Exception *** in pandas dimension_reset():"+str(e)

	def dimension_get_max_min(self, dimension_name):
		'''
			description:
				get_max_min for a dimension for pandas
			Get parameters:
				dimension_name (string)
			Response:
				max_min_tuple
		'''
		try:
			#max_min_tuple = (float(self.pandas_df[dimension_name].max()), float(self.pandas_df[dimension_name].min()))
			#return str(max_min_tuple)
			query = "select min(%s) as dmin, max(%s) as dmax from %s;" % (dimension_name, dimension_name, self.table)
			query = query.replace("==", "=")			
			print("******* %s *********" % query)
			
			with self.connection as c:
				c.execute(query)
				result = list(c)
			return str(result[0])
		except Exception as e:
			return "Exception *** in pandas dimension_get_max_min():"+str(e)

	def dimension_hist(self, dimension_name, num_of_bins):
		'''
			description:
				get histogram for a dimension for pandas
			Get parameters:
				dimension_name (string)
				num_of_bins (integer)
			Response:
				string(json) -> "{X:[__values_of_colName_with_max_64_bins__], Y:[__frequencies_per_bin__]}"
		'''
		try:
			num_of_bins = int(num_of_bins)
			if len(self.dimensions_filters.keys()) == 0 or (dimension_name not in self.dimensions_filters) or (dimension_name in self.dimensions_filters and self.dimensions_filters[dimension_name] == ''):
				#return str(self.hist_numpy(self.pandas_df[str(dimension_name)].values,num_of_bins))
				return str(self.hist_numpy(str(dimension_name), num_of_bins))
			else:
				#temp_df = self.reset_filters(self.back_up_dimension_pandas, omit=dimension_name)
				#return str(self.hist_numpy(temp_df[str(dimension_name)].values,num_of_bins))
				query = self.reset_filters(omit=dimension_name)
				return str(self.hist_numpy(str(dimension_name), num_of_bins, query))
		except Exception as e:
			return "Exception *** in pandas dimension_hist():"+str(e)
	
	# Seuraavaa ei käytetä ikinä ja se tuottaisi muutenkin ylisuuren tietueen (koko taulu json muodossa) 
	"""
	def dimension_filter_order(self, dimension_name, sort_order, num_rows, columns):
		'''
			description:
				get columns values by a filter_order(all, top(n), bottom(n)) sorted by dimension_name
			Get parameters:
				dimension_name (string)
				sort_order (string): top/bottom/all
				num_rows (integer): OPTIONAL -> if sort_order= top/bottom
				columns (string): comma separated column names
			Response:
				string(json) -> "{col_1:[__row_values__], col_2:[__row_values__],...}"
		'''
		try:
			columns = columns.split(',')
			if(len(columns) == 0 or columns[0]==''):
				#columns = list(self.pandas_df.columns)
				columns = self._get_columns()
			elif dimension_name not in columns:
				columns.append(dimension_name)
			if 'all' == sort_order:
				temp_df = self.pandas_df.loc[:,columns].to_dict()
			else:
				num_rows = int(num_rows)
				max_rows = max(len(self.pandas_df)-1,0)
				n_rows = min(num_rows, max_rows)
				if 'top' == sort_order:
					temp_df = self.pandas_df.loc[:,columns].nlargest(n_rows,[dimension_name]).to_dict()
				elif 'bottom' == sort_order:
					temp_df = self.pandas_df.loc[:,columns].nsmallest(n_rows,[dimension_name]).to_dict()

			return str(self.parse_dict(temp_df))

		except Exception as e:
			return "Exception *** in pandas dimension_filter_order():"+str(e)
	"""
	
	def dimension_filter(self, dimension_name, comparison_operation, value, pre_reset):
		'''
			description:
				cumulative filter dimension_name by comparison_operation and value
			Get parameters:
				dimension_name (string)
				comparison_operation (string)
				value (float/int)
			Response:
				number_of_rows_left
		'''
		try:
			if pre_reset == True:
				#implementation of resetThenFilter function
				self.dimension_reset(dimension_name)

			query = dimension_name+comparison_operation+value
			if dimension_name in self.dimensions_filters:
				if len(self.dimensions_filters[dimension_name])>0:
					self.dimensions_filters[dimension_name] += ' and '+ query
				else:
					self.dimensions_filters[dimension_name] = query
				self.dimensions_filters_response_format[dimension_name] = [value,value]
			
			return self._get_filtered_size(query);

		except Exception as e:
			return "Exception *** in pandas dimension_filter():"+str(e)

	def dimension_filter_range(self, dimension_name, min_value, max_value, pre_reset):
		'''
			description:
				cumulative filter_range dimension_name between range [min_value,max_value]
			Get parameters:
				dimension_name (string)
				min_value (integer)
				max_value (integer)
			Response:
				number_of_rows_left
		'''
		try:
			if pre_reset == True:
				#implementation of resetThenFilter function
				self.dimension_reset(dimension_name)

			query = dimension_name+">="+min_value+" and "+dimension_name+"<="+max_value
			if dimension_name in self.dimensions_filters:
				if len(self.dimensions_filters[dimension_name])>0:
					self.dimensions_filters[dimension_name] += ' and '+ query
				else:
					self.dimensions_filters[dimension_name] = query
				self.dimensions_filters_response_format[dimension_name] = [min_value,max_value]
			
			return self._get_filtered_size(query);

		except Exception as e:
			return "Exception *** in pandas dimension_filter_range():"+str(e)

	def groupby_load(self, dimension_name, groupby_agg, groupby_agg_key):
		'''
			description:
				load groupby operation for dimension as per the given groupby_agg
			input:
				dimension_name <string>:
				groupby_agg <dictionary>:
				groupby_agg_key <string>:
			return:
				status: groupby intialized successfully
		'''
		try:
			key = dimension_name+"_"+groupby_agg_key
			self.group_by_backups[key] = True
			response = 'groupby initialized successfully'
			return response
		except Exception as e:
			return 'Exception *** in pandas groupby_load():'+str(e)

	def groupby_filter_order(self, dimension_name, groupby_agg, groupby_agg_key, sort_order, num_rows, sort_column):
		'''
			description:
				get groupby values by a filter_order(all, top(n), bottom(n)) for a groupby on a dimension
			Get parameters:
				dimension_name (string)
				groupby_agg (JSON stringified object)
				groupby_agg_key <string>:
				sort_order (string): top/bottom/all
				num_rows (integer): OPTIONAL -> if sort_order= top/bottom
				sort_column: column name by which the result should be sorted
			Response:
				all rows/error => "groupby not initialized"
		'''
		try:
			key = dimension_name+"_"+groupby_agg_key
			if(key not in self.group_by_backups):
				res = "groupby not intialized"
			else:
				#removing the cumulative filters on the current dimension for the groupby

				#temp_df = self.reset_filters(self.back_up_dimension_pandas, omit=dimension_name)
				#self.groupby(temp_df,dimension_name,groupby_agg,groupby_agg_key)
				query = self.reset_filters(omit=dimension_name)
				self.groupby(query, dimension_name, groupby_agg, groupby_agg_key, sort_order, num_rows, sort_column)
				"""
				if 'all' == sort_order:
					temp_df = self.group_by_backups[key].to_dict()
				else:
					max_rows = max(len(self.group_by_backups[key])-1,0)
					n_rows = min(num_rows,max_rows)
					try:
						if 'top' == sort_order:
							temp_df = self.group_by_backups[key].nlargest(n_rows,[sort_column]).to_dict()
						elif 'bottom' == sort_order:
							temp_df = self.group_by_backups[key].nsmallest(n_rows,[sort_column]).to_dict()
					except Exception as e:
						return 'Exception *** '+str(e)
				"""
			
				temp_dict = self.group_by_backups[key]
				res = json.dumps(temp_dict,default=self.default)
			return res

		except Exception as e:
			return 'Exception *** in pandas groupby_filter_order():'+str(e)

	# Internal functions
	
	def _get_filtered_size(self, query = ""):
		'''
			description:
				get shape of the dataframe
			input:
				query: query as string
			return:
				row count as string
		'''
		if (len(query) > 0):
			query = "select count() as size from %s where %s;" % (self.table, query)
		else:
			query = "select count() as size from %s;" % (self.table)
			
		try:
			query = query.replace("==", "=")
			print("******* %s *********" % query)
			
			with self.connection as c:
				c.execute(query)
				result = list(c)
			return str(result[0][0])
		except Exception as e:
			return "Exception *** in pandas get_size():"+str(e)
			
	def _get_columns(self):
		'''
			description:
				Column names in a data frame
			Output:
				list of column names
		'''
		try:
			response = list(c[0] for c in self.connection.get_table_details(self.table))
		except Exception as e:
			response = "Exception *** in omnisci get_columns():"+str(e)
		return response
