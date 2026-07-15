from .models import Asset, Relationship


class EnterpriseGraph:

    def __init__(self):

        self.assets = {}

        self.relationships = []

    def add_asset(self, asset: Asset):

        self.assets[asset.id] = asset

    def add_relationship(self, relationship: Relationship):

        self.relationships.append(relationship)

    def get_asset(self, asset_id):

        return self.assets.get(asset_id)

    def get_downstream(self, asset_id):

        return [

            r.target

            for r in self.relationships

            if r.source == asset_id

        ]

    def get_upstream(self, asset_id):

        return [

            r.source

            for r in self.relationships

            if r.target == asset_id

        ]