import logging
import warnings

import numpy as np
import os
import re
import typing
import scipy.sparse
from typing import Any, Dict, Optional, Text, Union, List

from rasa.nlu import utils
from rasa.nlu.config import RasaNLUModelConfig
from rasa.nlu.featurizers.featurzier import Featurizer
from rasa.nlu.training_data import Message, TrainingData
import rasa.utils.io
from rasa.nlu.constants import (
    TOKENS_NAMES,
    TEXT_ATTRIBUTE,
    RESPONSE_ATTRIBUTE,
    SPARSE_FEATURE_NAMES,
)
from rasa.constants import DOCS_BASE_URL

logger = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    from rasa.nlu.model import Metadata


class RegexFeaturizer(Featurizer):

    provides = [SPARSE_FEATURE_NAMES[TEXT_ATTRIBUTE]]

    requires = [TOKENS_NAMES[TEXT_ATTRIBUTE]]

    defaults = {
        # if True return a sequence of features (return vector has size
        # token-size x feature-dimension)
        # if False token-size will be equal to 1
        "return_sequence": False
    }

    def __init__(
        self,
        component_config: Optional[Dict[Text, Any]] = None,
        known_patterns: Optional[List[Dict[Text, Text]]] = None,
        lookup_tables: Optional[List[Dict[Text, Union[Text, List]]]] = None,
    ) -> None:

        super().__init__(component_config)

        self.known_patterns = known_patterns if known_patterns else []
        lookup_tables = lookup_tables or []
        self._add_lookup_table_regexes(lookup_tables)

        self.return_sequence = self.component_config["return_sequence"]

    def train(
        self, training_data: TrainingData, config: RasaNLUModelConfig, **kwargs: Any
    ) -> None:

        self.known_patterns = training_data.regex_features
        self._add_lookup_table_regexes(training_data.lookup_tables)

        for example in training_data.training_examples:
            for attribute in [TEXT_ATTRIBUTE, RESPONSE_ATTRIBUTE]:
                self._text_features_with_regex(example, attribute)

    def process(self, message: Message, **kwargs: Any) -> None:
        self._text_features_with_regex(message, TEXT_ATTRIBUTE)

    def _text_features_with_regex(self, message: Message, attribute: Text) -> None:
        if self.known_patterns:
            extras = self._features_for_patterns(message, attribute)
            features = self._combine_with_existing_sparse_features(
                message, extras, feature_name=SPARSE_FEATURE_NAMES[attribute]
            )
            message.set(SPARSE_FEATURE_NAMES[attribute], features)

    def _add_lookup_table_regexes(
        self, lookup_tables: List[Dict[Text, Union[Text, List]]]
    ) -> None:
        """appends the regex features from the lookup tables to self.known_patterns"""
        for table in lookup_tables:
            regex_pattern = self._generate_lookup_regex(table)
            lookup_regex = {"name": table["name"], "pattern": regex_pattern}
            self.known_patterns.append(lookup_regex)

    def _features_for_patterns(
        self, message: Message, attribute: Text
    ) -> scipy.sparse.coo_matrix:
        """Checks which known patterns match the message.

        Given a sentence, returns a vector of {1,0} values indicating which
        regexes did match. Furthermore, if the
        message is tokenized, the function will mark all tokens with a dict
        relating the name of the regex to whether it was matched."""
        tokens = message.get(TOKENS_NAMES[attribute], [])

        if self.return_sequence:
            seq_length = len(tokens)
        else:
            seq_length = 1

        vec = np.zeros([seq_length, len(self.known_patterns)])

        for pattern_index, pattern in enumerate(self.known_patterns):
            matches = re.finditer(pattern["pattern"], message.text)
            matches = list(matches)

            for token_index, t in enumerate(tokens):
                patterns = t.get("pattern", default={})
                patterns[pattern["name"]] = False

                if self.return_sequence:
                    seq_index = token_index
                else:
                    seq_index = 0

                for match in matches:
                    if t.offset < match.end() and t.end > match.start():
                        patterns[pattern["name"]] = True
                        vec[seq_index][pattern_index] = 1.0

                t.set("pattern", patterns)

        return scipy.sparse.coo_matrix(vec)

    def _generate_lookup_regex(
        self, lookup_table: Dict[Text, Union[Text, List[Text]]]
    ) -> Text:
        """creates a regex out of the contents of a lookup table file"""
        lookup_elements = lookup_table["elements"]
        elements_to_regex = []

        # if it's a list, it should be the elements directly
        if isinstance(lookup_elements, list):
            elements_to_regex = lookup_elements
            warnings.warn(
                f"Directly including lookup tables as a list is deprecated since Rasa "
                f"1.6. See {DOCS_BASE_URL}/nlu/training-data-format/#lookup-tables "
                f"how to do so.",
                FutureWarning,
            )

        # otherwise it's a file path.
        else:

            try:
                f = open(lookup_elements, "r", encoding=rasa.utils.io.DEFAULT_ENCODING)
            except OSError:
                raise ValueError(
                    "Could not load lookup table {}"
                    "Make sure you've provided the correct path".format(lookup_elements)
                )

            with f:
                for line in f:
                    new_element = line.strip()
                    if new_element:
                        elements_to_regex.append(new_element)

        # sanitize the regex, escape special characters
        elements_sanitized = [re.escape(e) for e in elements_to_regex]

        # regex matching elements with word boundaries on either side
        regex_string = "(?i)(\\b" + "\\b|\\b".join(elements_sanitized) + "\\b)"
        return regex_string

    @classmethod
    def load(
        cls,
        meta: Dict[Text, Any],
        model_dir: Optional[Text] = None,
        model_metadata: Optional["Metadata"] = None,
        cached_component: Optional["RegexFeaturizer"] = None,
        **kwargs: Any,
    ) -> "RegexFeaturizer":

        file_name = meta.get("file")
        regex_file = os.path.join(model_dir, file_name)

        if os.path.exists(regex_file):
            known_patterns = rasa.utils.io.read_json_file(regex_file)
            return RegexFeaturizer(meta, known_patterns=known_patterns)
        else:
            return RegexFeaturizer(meta)

    def persist(self, file_name: Text, model_dir: Text) -> Optional[Dict[Text, Any]]:
        """Persist this model into the passed directory.

        Return the metadata necessary to load the model again."""
        file_name = file_name + ".pkl"
        regex_file = os.path.join(model_dir, file_name)
        utils.write_json_to_file(regex_file, self.known_patterns, indent=4)

        return {"file": file_name}