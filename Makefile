DBHOST=192.168.0.4
DBNAME=test_autoreg_dev

msg:
	./autoreg/arf/manage.py makemessages -v3 -a -e html,mail,py --ignore build
compilemsg:
	./autoreg/arf/manage.py compilemessages

install-locale:
	(tar cf - locale) | (cd /usr/local/autoreg/arf/; tar xfv -)

install-templates:
	tar cf - templates | (cd /usr/local/autoreg/arf/; tar xfv -)

preparedb:
	(echo "DROP DATABASE $(DBNAME);"; \
	echo "CREATE DATABASE $(DBNAME);"; \
	echo "\\c $(DBNAME)";		\
	cat postgres/autoreg.schema postgres/init.sql postgres/test-fixtures.sql; \
	echo "BEGIN;"; ./tools/mkiso.py; echo "COMMIT;") \
	| psql -h $(DBHOST) --user autoreg postgres

test:	preparedb
	coverage-3.6 erase
	-PYTHONPATH=$(HOME)/autoreg ENCRYPT_KEY='qVMjRVe9fQ-jF7G06l4Fogjqu_PjONV70KvZprhtit0=' \
		coverage-3.6 run --source='.' ./autoreg/arf/manage.py test --keepdb --settings autoreg.arf.arf.debugsettings
	-PYTHONPATH=$(HOME)/autoreg ENCRYPT_KEY='qVMjRVe9fQ-jF7G06l4Fogjqu_PjONV70KvZprhtit0=' \
		AUTOREG_DBSTRING="dbname=$(DBNAME) host=$(DBHOST) user=autoreg password=" \
		coverage-3.6 run --source='.' -a -m unittest discover tests
	coverage-3.6 report
	coverage-3.6 html
