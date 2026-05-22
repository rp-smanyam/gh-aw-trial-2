import asyncio
import uuid
from datetime import datetime

from agent_leasing.api.model import Persona
from agent_leasing.kafka.kafka_context import kafka_application_context
from agent_leasing.kafka.kafka_recorder import (
    Author,
    Channel,
    Flow,
    log_data_curation_event,
)


async def produce_sample_data_curation_events():
    """
    Produce sample inbound and outbound data curation events.

    This is provided solely for testing Kafka interaction.
    """
    kafka_application_context.start()

    conversation_type = Channel.CHAT
    call_sid = None
    property_id = "21521"
    applicant_id = "740473"
    bot_type = Persona.RESIDENT

    inbound_events = ["hello"]
    outbound_events = ["How can I help you today?"]
    number_of_test_event_pairs = 1
    flows = [Flow(name="test_flow")]

    for i in range(0, number_of_test_event_pairs):
        chat_session_id = str(uuid.uuid4())

        for body in inbound_events:
            await log_data_curation_event(
                chat_session_id=chat_session_id,
                conversation_type=conversation_type,
                body=body,
                call_sid=call_sid,
                property_id=property_id,
                applicant_id=applicant_id,
                bot_type=bot_type,
                author=Author.CONTACT,
                flows=flows,
                timestamp=datetime.now(),
                validate_record=True,
            )

        for body in outbound_events:
            await log_data_curation_event(
                chat_session_id=chat_session_id,
                conversation_type=conversation_type,
                body=body,
                call_sid=call_sid,
                property_id=property_id,
                applicant_id=applicant_id,
                bot_type=bot_type,
                author=Author.BOT,
                flows=flows,
                timestamp=datetime.now(),
                validate_record=True,
            )

    kafka_application_context.close()


def main():
    asyncio.run(produce_sample_data_curation_events())


if __name__ == "__main__":
    main()
