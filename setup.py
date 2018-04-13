from GoSync.defines import *
from codecs import open
from os import path
from setuptools import find_packages
from setuptools import setup


here = path.abspath(path.dirname(__file__))
###################################################################
# Get the long description from the README file
with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name=APP_NAME,
    version=APP_VERSION,
    description=APP_DESCRIPTION,
    long_description=long_description,
    url=APP_WEBSITE,
    author=APP_DEVELOPER,
    author_email=APP_DEVELOPER_EMAIL,
    license='GPL',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)',
        'Programming Language :: Python :: 2.7',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
    ],
    keywords='Google Drive client Linux Python',
    packages=find_packages(exclude=['contrib', 'docs', 'tests']),

    package_data={
        'GoSync': ['resources/*.png'],
    },

    install_requires=['google-api-python-client', 'pydrive', 'watchdog'],
    entry_points={
        'console_scripts': [
            'GoSync=GoSync.GoSync:main',
        ],
    },
)
