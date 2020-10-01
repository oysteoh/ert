import graphene
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from graphene import relay
from graphene_sqlalchemy import SQLAlchemyObjectType, SQLAlchemyConnectionField
from ert_shared.storage.model import Project as ProjectModel, Ensemble as EnsembleModel, Realization as RealizationModel, Response as ResponseModel, ResponseDefinition as ResponseDefinitionModel, Entities

engine = create_engine('sqlite:///entities.db', convert_unicode=True)
db_session = scoped_session(sessionmaker(autocommit=False,
                                         autoflush=False,
                                         bind=engine))

Entities.query = db_session.query_property()

class Project(SQLAlchemyObjectType):
    class Meta:
        model = ProjectModel
        interfaces = (relay.Node, )

class Ensemble(SQLAlchemyObjectType):
    class Meta:
        model = EnsembleModel
        interfaces = (relay.Node, )

class Realization(SQLAlchemyObjectType):
    class Meta:
        model = RealizationModel
        interfaces = (relay.Node, )

class Response(SQLAlchemyObjectType):
    class Meta:
        model = ResponseModel
        interfaces = (relay.Node, )

class ResponseDefinition(SQLAlchemyObjectType):
    class Meta:
        model = ResponseDefinitionModel
        interfaces = (relay.Node, )







class Query(graphene.ObjectType):
    node = relay.Node.Field()
    # Allows sorting over multiple columns, by default over the primary key
    project = SQLAlchemyConnectionField(Project.connection)
    ensemble = SQLAlchemyConnectionField(Ensemble.connection)
    realization = SQLAlchemyConnectionField(Realization.connection)
    response = SQLAlchemyConnectionField(Response.connection)
    response_definition = SQLAlchemyConnectionField(ResponseDefinition.connection)

schema = graphene.Schema(query=Query)