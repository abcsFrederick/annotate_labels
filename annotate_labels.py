import itertools
import json
import operator
import os.path
import tempfile

import girder_client

from PIL import Image, ImageDraw


class LabelAnnotator(object):
    def __init__(self, client):
        self.client = client

        self._colormap = None
        self._girder_id = None
        self._is_overlay_id = False
        self._item_id = None
        self._overlay_item_id = None
        self._overlay_file = None
        self._annotations = None

    @property
    def colormap(self):
        return self._colormap

    @colormap.setter
    def colormap(self, colormap_filename):
        if not colormap_filename:
            self._colormap = None
            return

        with open(colormap_filename) as f:
            self._colormap = json.load(f)

    @property
    def girder_id(self):
        return self._girder_id

    @girder_id.setter
    def girder_id(self, girder_id):
        self._gider_id = girder_id
        self._item_id = None
        self._overlay_item_id = None

        if girder_id is not None:
            if self._is_overlay_id:
                overlay = self.client.get('overlay/%s' % girder_id)
                item_id = overlay['itemId']
            else:
                item_id = girder_id
                parameters = {'itemId': item_id}
                overlays = self.client.get('overlay', parameters=parameters)
                if not overlays:
                    raise ValueError('no overlay for item %s' % item_id)
                if len(overlays) > 1:
                    raise ValueError('multiple overlays, specifiy overlay')
                overlay = overlays[0]

            self._item_id = item_id
            self._overlay_item_id =  overlay['overlayItemId']

        self._update_overlay_file()
        self._update_annotations()

    @property
    def is_overlay_id(self):
        return self._is_overlay_id

    @is_overlay_id.setter
    def is_overlay_id(self, is_overlay_id):
        self._is_overlay_id = is_overlay_id

    @property
    def item_id(self):
        return self._item_id

    @property
    def overlay_item_id(self):
        return self._overlay_item_id

    @property
    def overlay_file(self):
        return self._overlay_file

    def _update_overlay_file(self):
        if self._overlay_item_id is None:
            self._overlay_file = None
            return

        path = 'item/%s/files' % self._overlay_item_id
        overlay_files = self.client.get(path, parameters={'limit': 2})
        if not overlay_files:
            raise ValueError('no files for overlay %s' % self._overlay_item_id)

        overlay_files = list(filter(lambda o: o['mimeType'].startswith('image'), overlay_files))
        if len(overlay_files) > 1:
            overlay_item = self.client.getItem(self._overlay_item_id)
            try:
                file_id = overlay_item['largeImage']['fileId']
            except KeyError:
                file_id = None
            overlay_files = list(filter(lambda o: o['_id'] != file_id, overlay_files))
        if len(overlay_files) > 1:
            raise ValueError('multiple files for overlay %s' % self._overlay_item_id)
        self._overlay_file = overlay_files[0]


    def _update_annotations(self):
        if self._item_id is None:
            self._annotations = None
            return
        path = 'annotation/item/%s' % self._item_id
        try:
            self._annotations = client.get(path)
        except girder_client.HttpError as e:
            if e.status == 400:
                parameters = {'itemId': self._item_id, 'limit': 0}
                annotations = client.get('annotation', parameters=parameters)
                self._annotations = []
                for annotation in annotations:
                    annotation = client.get('annotation/%s' %
                                            annotation['_id'])
                    self._annotations.append(annotation)
            else:
                raise


    def _annotations_iterator(self):
        if self._annotations is None:
            return

        if self._colormap is None:
            raise ValueError('no color mapping set')

        def keyfunc(annotation):
            if 'name' not in annotation['annotation']:
                return
            return annotation['annotation']['name']

        iterator = itertools.groupby(self._annotations, keyfunc)
        for name, annotation_group in iterator:
            if name is None:
                continue
            try:
                color = self._colormap[name]
            except KeyError:
                raise ValueError('no color mapping for annotation "%s"' % name)
            yield color, annotation_group

    @property
    def annotations(self):
        return self._annotations_iterator()

    def _draw_annotations(self):
        image = self.image
        draw = ImageDraw.Draw(image)

        for color, annotation_group in self.annotations:
            for annotation in annotation_group:
                for element in annotation['annotation']['elements']:
                    if element['type'] == 'point':
                        xy = [tuple(element['center'][:2])]
                        draw.point(xy, fill=color)
                    elif element['type'] == 'rectangle':
                        xy = [
                            tuple(element['center'][:2]),
                            (element['center'][0] + element['width'],
                             element['center'][1] + element['height'])
                        ]
                        draw.rectangle(xy, outline=color, fill=color)
                    elif element['type'] == 'polyline':
                        xy = [tuple(point[:2]) for point in element['points']]
                        draw.polygon(xy, outline=color, fill=color)
                    else:
                        raise ValueError('invalid element type: %s' %
                                         element['type'])
        return image

    @property
    def image_file(self):
        if self._overlay_file is None:
            return
        extension = os.path.splitext(self._overlay_file['name'])[1]
        image_file = tempfile.NamedTemporaryFile(suffix=extension)
        self.client.downloadFile(self._overlay_file['_id'], image_file.name)
        return image_file

    @property
    def image(self):
        image_file = self.image_file

        try:
            image = Image.open(image_file.name)
        except (IOError, OSError) as pillow_exception:
            try:
                import numpy
                import pytiff
                image = pytiff.Tiff(image_file.name)
                image = Image.fromarray(numpy.array(image))
            except (IOError, OSError) as pytiff_exception:
                raise

        image_file.close()

        return image


    def save(self, output_filename):
        self._draw_annotations().save(output_filename)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('url', help='The full path to the REST API of a Girder'
                        ' instance, e.g. http://my.girder.com/api/v1')
    parser.add_argument('username')
    parser.add_argument('--password')
    parser.add_argument('id', help='Girder item id of annotated image')
    parser.add_argument('colormap', help='JSON file mapping annotation names'
                       ' to color/label values')
    parser.add_argument('output', help='filename of the output image')
    parser.add_argument('--overlay', action='store_true', help='Girder item id'
                        ' is for the overlay')
    args = parser.parse_args()

    client = girder_client.GirderClient(apiUrl=args.url)
    if args.password:
        client.authenticate(args.username, args.password)
    else:
        client.authenticate(username=args.username, interactive=True)

    label_annotator = LabelAnnotator(client)
    label_annotator.colormap = args.colormap
    label_annotator.is_overlay_id = args.overlay
    label_annotator.girder_id = args.id
    label_annotator.save(args.output)
