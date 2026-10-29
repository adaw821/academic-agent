"""Explicit assignment attachment provenance, persistence and scoped filtering."""
import unittest
import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import test_resource_status as fixture
from app.models.course_resources import CourseResource
from app.models.assignments import Assignment
from app.services.ispace import ISpaceClient, ORIGIN


class AttachmentCollectionTests(unittest.TestCase):
    def collect(self, shared=False):
        file = '/pluginfile.php/12/mod_assign/intro/questions.pdf'
        def handler(request):
            if request.url.path == '/course/view.php':
                body = f'<h1>Course</h1><div id="region-main"><div class="course-content"><a href="{file}">Questions</a>'
                for ident in (1, 2):
                    body += f'<li class="activity"><a href="/mod/assign/view.php?id={ident}">Lab {ident}</a></li>'
                body += '</div></div>'
            else:
                linked = file if request.url.params['id'] == '1' or shared else '/pluginfile.php/12/mod_assign/intro/other.pdf'
                body = f'<div id="region-main"><h2>Lab</h2><div id="intro"><a href="{linked}">Questions</a><a href="{linked}?forcedownload=1">Duplicate</a><a href="/mod/page/view.php?id=10">Page</a></div><a href="/pluginfile.php/12/assignsubmission_file/submission_files/answer.pdf">Submission</a></div>'
                if request.url.path == '/mod/page/view.php':
                    body = '<div id="region-main"><a href="/pluginfile.php/12/lecture.pdf">Indirect lecture</a></div>'
            return httpx.Response(200, text=body, headers={'content-type': 'text/html'})
        with ISpaceClient('test', transport=httpx.MockTransport(handler)) as client:
            return client.collect_course(ORIGIN + '/course/view.php?id=1')

    def test_direct_attachment_metadata_survives_course_page_dedup(self):
        snapshot = self.collect()
        questions = next(row for row in snapshot.resources if row['source_url'].endswith('/questions.pdf'))
        self.assertEqual(questions['assignment_external_ids'], ['1'])
        lecture = next(row for row in snapshot.resources if row['source_url'].endswith('/lecture.pdf'))
        self.assertEqual(lecture['assignment_external_ids'], [])
        self.assertFalse(any('assignsubmission_' in row['source_url'] for row in snapshot.resources))

    def test_shared_attachment_records_all_references(self):
        snapshot = self.collect(shared=True)
        questions = [row for row in snapshot.resources if row['source_url'].endswith('/questions.pdf')]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]['assignment_external_ids'], ['1', '2'])


class AssignmentResourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.ResourceStatusTests.asyncSetUp(self)
        self.snapshot.assignments.append(dict(self.snapshot.assignments[0], external_activity_id='2', title='Second lab'))
        self.resources[0]['assignment_external_ids'] = ['1']
        self.resources[1]['assignment_external_ids'] = ['2']

    asyncTearDown = fixture.ResourceStatusTests.asyncTearDown
    post = fixture.ResourceStatusTests.post
    sync = fixture.ResourceStatusTests.sync
    get = fixture.ResourceStatusTests.get

    async def test_linking_repeat_sync_and_assignment_filter(self):
        await self.sync(self.resources)
        first = (await self.get()).json()['items']
        self.assertEqual([row['assignment_id'] for row in first], [1, 2])
        self.assertTrue(all(row['purpose'] == 'assignment_attachment' for row in first))
        await self.sync(self.resources)
        self.assertEqual([row['id'] for row in (await self.get()).json()['items']], [row['id'] for row in first])
        result = (await self.get({'assignment_id': 1, 'source_status': 'seen', 'limit': 1})).json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['assignment_id'], 1)
        self.assertEqual((await self.get({'assignment_id': 1, 'offset': 1})).json()['items'], [])
        self.assertEqual((await self.get({'assignment_id': 5})).json()['total'], 0)  # Other user's assignment.
        self.assertEqual((await self.get({'assignment_id': 0})).status_code, 422)

    async def test_reference_removed_and_moved_clears_only_automatic_link(self):
        await self.sync(self.resources)
        ident = (await self.get()).json()['items'][0]['id']
        with Session(self.engine) as db:
            db.execute(update(CourseResource).where(CourseResource.id == ident).values(storage_path='retained-cache.pdf'))
            db.commit()
        self.resources[0]['assignment_external_ids'] = []
        await self.sync(self.resources)
        row = (await self.get()).json()['items'][0]
        self.assertIsNone(row['assignment_id'])
        self.assertEqual(row['purpose'], 'unknown')
        self.resources[0]['assignment_external_ids'] = ['2']
        await self.sync(self.resources)
        with Session(self.engine) as db:
            row = db.execute(select(CourseResource.__table__).where(CourseResource.id == ident)).mappings().one()
            self.assertEqual(row['assignment_id'], 2)
            self.assertEqual(row['storage_path'], 'retained-cache.pdf')

    async def test_new_school_activity_ids_resolve_to_local_assignment_ids(self):
        for index, external in enumerate(('901', '902')):
            self.snapshot.assignments[index]['external_activity_id'] = external
            self.resources[index]['assignment_external_ids'] = [external]
        await self.sync(self.resources)
        with Session(self.engine) as db:
            local_ids = dict(db.execute(select(Assignment.external_activity_id, Assignment.id)
                                        .where(Assignment.course_id == 1, Assignment.external_activity_id.in_(['901', '902']))).all())
        rows = (await self.get()).json()['items']
        self.assertEqual([row['assignment_id'] for row in rows], [local_ids['901'], local_ids['902']])
        self.assertNotEqual(rows[0]['assignment_id'], 901)

    async def test_shared_attachment_does_not_arbitrarily_choose_assignment(self):
        await self.sync(self.resources)
        self.resources[0]['assignment_external_ids'] = ['1', '2']
        self.snapshot.resources = self.resources
        response = await self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['warnings'])
        self.assertIsNone((await self.get()).json()['items'][0]['assignment_id'])
        self.resources[0]['assignment_external_ids'] = ['1', '1']
        await self.sync(self.resources)
        self.assertEqual((await self.get()).json()['items'][0]['assignment_id'], 1)

    async def test_manual_classification_or_link_is_preserved(self):
        await self.sync(self.resources)
        rows = (await self.get()).json()['items']
        with Session(self.engine) as db:
            db.execute(update(CourseResource).where(CourseResource.id == rows[0]['id']).values(purpose='lecture', assignment_id=None))
            db.execute(update(CourseResource).where(CourseResource.id == rows[1]['id']).values(purpose='unknown', assignment_id=1))
            db.commit()
        await self.sync(self.resources)
        rows = (await self.get()).json()['items']
        self.assertEqual(rows[0]['purpose'], 'lecture')
        self.assertIsNone(rows[0]['assignment_id'])
        self.assertEqual(rows[1]['assignment_id'], 1)

    async def test_invalid_reference_rolls_back_whole_sync(self):
        await self.sync(self.resources)
        before = (await self.get()).json()
        self.resources[1]['assignment_external_ids'] = ['missing']
        self.snapshot.resources = self.resources
        self.assertEqual((await self.post()).status_code, 500)
        self.assertEqual((await self.get()).json(), before)

    async def test_legacy_snapshot_and_absent_resources_keep_historical_links(self):
        await self.sync(self.resources)
        legacy = [dict(self.resources[0])]
        legacy[0].pop('assignment_external_ids')
        await self.sync(legacy)
        rows = (await self.get()).json()['items']
        self.assertEqual([row['assignment_id'] for row in rows], [1, 2])
        self.assertEqual(rows[1]['source_status'], 'not_seen')


if __name__ == '__main__':
    unittest.main()
