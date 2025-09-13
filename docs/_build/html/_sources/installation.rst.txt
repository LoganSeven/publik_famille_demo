Installation Guide
==================

Requirements
------------

* Python 3.12+
* Django 5.2+
* SQLite (default) or PostgreSQL
* Virtualenv or venv recommended

Steps
-----

1. Clone the repository:

   .. code-block:: bash

      git clone https://github.com/your-org/publik_famille_demo.git
      cd publik_famille_demo

2. Create and activate a virtual environment:

   .. code-block:: bash

      python -m venv venv
      source venv/bin/activate

3. Install dependencies:

   .. code-block:: bash

      pip install -r requirements.txt

4. Run migrations:

   .. code-block:: bash

      python manage.py migrate

5. Load demo data:

   .. code-block:: bash

      python manage.py bootstrap_demo

6. Start the development server:

   .. code-block:: bash

      python manage.py runserver
