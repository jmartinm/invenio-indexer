# -*- coding: utf-8 -*-
#
# This file is part of Invenio.
# Copyright (C) 2016 CERN.
#
# Invenio is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# Invenio is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Invenio; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.
#
# In applying this license, CERN does not
# waive the privileges and immunities granted to it by virtue of its status
# as an Intergovernmental Organization or submit itself to any jurisdiction.

"""Test API."""

from __future__ import absolute_import, print_function

import uuid

from celery.messaging import establish_connection
from invenio_db import db
from invenio_records.api import Record
from kombu.compat import Consumer
from mock import MagicMock, patch

from invenio_indexer.api import RecordIndexer
from invenio_indexer.signals import before_record_index


def test_indexer_bulk_index(app, queue):
    """Test delay indexing."""
    with app.app_context():
        with establish_connection() as c:
            indexer = RecordIndexer()
            id1 = uuid.uuid4()
            id2 = uuid.uuid4()
            indexer.bulk_index([id1, id2])
            indexer.bulk_delete([id1, id2])

            consumer = Consumer(
                connection=c,
                queue=indexer.mq_queue.name,
                exchange=indexer.mq_exchange.name,
                routing_key=indexer.mq_routing_key)

            messages = list(consumer.iterqueue())
            [m.ack() for m in messages]

            assert len(messages) == 4
            data0 = messages[0].decode()
            assert data0['id'] == str(id1)
            assert data0['op'] == 'index'
            data2 = messages[2].decode()
            assert data2['id'] == str(id1)
            assert data2['op'] == 'delete'


def test_delete_action(app):
    """Test delete action."""
    with app.app_context():
        testid = str(uuid.uuid4())
        action = RecordIndexer()._delete_action(
            dict(id=testid, op='delete', index='idx', doc_type='doc'))
        assert action['_op_type'] == 'delete'
        assert action['_index'] == 'idx'
        assert action['_type'] == 'doc'
        assert action['_id'] == testid

        with patch('invenio_indexer.api.Record.get_record') as r:
            r.return_value = {'$schema': {
                '$ref': '/records/authorities/authority-v1.0.0.json'
            }}
            action = RecordIndexer()._delete_action(
                dict(id='myid', op='delete', index=None, doc_type=None))
            assert action['_op_type'] == 'delete'
            assert action['_index'] == 'records-authorities-authority-v1.0.0'
            assert action['_type'] == 'authority-v1.0.0'
            assert action['_id'] == 'myid'


def test_index_action(app):
    """Test index action."""
    with app.app_context():
        record = Record.create({'title': 'Test'})
        db.session.commit()

        def receiver(sender, json=None, record=None):
            json['extra'] = 'extra'

        with before_record_index.connected_to(receiver):
            action = RecordIndexer()._index_action(dict(
                id=str(record.id),
                op='index',
            ))
            assert action['_op_type'] == 'index'
            assert action['_index'] == app.config['INDEXER_DEFAULT_INDEX']
            assert action['_type'] == app.config['INDEXER_DEFAULT_DOC_TYPE']
            assert action['_id'] == str(record.id)
            assert action['_version'] == record.revision_id
            assert action['_version_type'] == 'external_gte'
            assert 'title' in action['_source']
            assert 'extra' in action['_source']


def test_process_bulk_queue(app, queue):
    """Test process indexing."""
    with app.app_context():
        # Create a test record
        r = Record.create({'title': 'test'})
        db.session.commit()
        invalid_id2 = uuid.uuid4()

        RecordIndexer().bulk_index([r.id, invalid_id2])
        RecordIndexer().bulk_delete([r.id, invalid_id2])

        ret = {}

        def _mock_bulk(client, actions_iterator, **kwargs):
            ret['actions'] = list(actions_iterator)
            return len(ret['actions'])

        with patch('invenio_indexer.api.bulk', _mock_bulk):
            # Invalid actions are rejected
            assert RecordIndexer().process_bulk_queue() == 2
            assert [x['_op_type'] for x in ret['actions']] == \
                ['index', 'delete']


def test_index(app):
    """Test record indexing."""
    with app.app_context():
        recid = uuid.uuid4()
        record = Record.create({'title': 'Test'}, id_=recid)
        db.session.commit()

        client_mock = MagicMock()
        RecordIndexer(search_client=client_mock, version_type='force').index(
            record)

        client_mock.index.assert_called_with(
            id=str(recid),
            version=0,
            version_type='force',
            index=app.config['INDEXER_DEFAULT_INDEX'],
            doc_type=app.config['INDEXER_DEFAULT_DOC_TYPE'],
            body={'title': 'Test'},
        )

        with patch('invenio_indexer.api.RecordIndexer.index') as fun:
            RecordIndexer(search_client=client_mock).index_by_id(recid)
            assert fun.called


def test_delete(app):
    """Test record indexing."""
    with app.app_context():
        recid = uuid.uuid4()
        record = Record.create({'title': 'Test'}, id_=recid)
        db.session.commit()

        client_mock = MagicMock()
        RecordIndexer(search_client=client_mock).delete(record)

        client_mock.delete.assert_called_with(
            id=str(recid),
            index=app.config['INDEXER_DEFAULT_INDEX'],
            doc_type=app.config['INDEXER_DEFAULT_DOC_TYPE'],
        )

        with patch('invenio_indexer.api.RecordIndexer.delete') as fun:
            RecordIndexer(search_client=client_mock).delete_by_id(recid)
            assert fun.called
