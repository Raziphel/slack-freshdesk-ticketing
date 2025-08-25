import json
from pathlib import Path

TYPE_MAP = {
    'custom_text': 'text',
    'default_subject': 'text',
    'default_description': 'text',
    'custom_paragraph': 'text',
    'custom_number': 'number',
    'custom_decimal': 'number',
    'custom_date': 'date',
    'custom_dropdown': 'select',
    'default_ticket_type': 'select',
    'default_priority': 'select',
    'default_status': 'select',
    'default_source': 'select',
    'default_group': 'select',
    'default_agent': 'select',
    'default_product': 'select',
    'default_company': 'select',
    'custom_checkbox': 'checkbox',
    'nested_field': 'text',
}


def normalize_type(field_type: str) -> str:
    return TYPE_MAP.get(field_type, 'text')


def build_question(field: dict) -> dict:
    question = {
        'id': field.get('name'),
        'question': field.get('label_for_customers') or field.get('label'),
        'type': normalize_type(field.get('type')),
        'required': field.get('required_for_customers', False),
    }
    choices = field.get('choices')
    if question['type'] in {'select', 'checkbox'} and choices:
        if isinstance(choices, dict):
            question['options'] = list(choices.keys())
        else:
            question['options'] = choices
    return question


def main() -> None:
    ticket_fields_path = Path('ticket_fields.json')
    question_flow_path = Path('question_flow.json')

    with ticket_fields_path.open() as f:
        fields = json.load(f)

    questions = [build_question(f) for f in fields]

    with question_flow_path.open('w') as f:
        json.dump(questions, f, indent=2)


if __name__ == '__main__':
    main()
