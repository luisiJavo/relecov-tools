#!/usr/bin/env python

import logging
import rich.console
import os
import sys
import relecov_tools.utils
from relecov_tools.config_json import ConfigJson
from yaml import YAMLError

# import relecov_tools.json_schema

log = logging.getLogger(__name__)
stderr = rich.console.Console(
    stderr=True,
    style="dim",
    highlight=False,
    force_terminal=relecov_tools.utils.rich_force_colors(),
)


class BioinfoMetadata:
    def __init__(
        self,
        json_file=None,
        input_folder=None,
        output_folder=None,
    ):
        if json_file is None:
            json_file = relecov_tools.utils.prompt_path(
                msg="Select the json file that was created by the read-lab-metadata"
            )
        if not os.path.isfile(json_file):
            log.error("json file %s does not exist ", json_file)
            stderr.print(f"[red] file {json_file} does not exist")
            sys.exit(1)
        self.json_file = json_file

        if input_folder is None:
            self.input_folder = relecov_tools.utils.prompt_path(
                msg="Select the input folder"
            )
        else:
            self.input_folder = input_folder
        if output_folder is None:
            self.output_folder = relecov_tools.utils.prompt_path(
                msg="Select the output folder"
            )
        else:
            self.output_folder = output_folder

        config_json = ConfigJson()
        self.configuration = config_json
        required_files = self.configuration.get_topic_data(
            "bioinfo_analysis", "required_file"
        )
        self.req_files = {}
        for key, file in required_files.items():
            f_path = os.path.join(self.input_folder, file)
            if not os.path.isfile(f_path):
                log.error("File %s does not exist", file)
                stderr.print(f"[red]File {file} does not exist")
                sys.exit(1)
            self.req_files[key] = f_path

    def add_fixed_values(self, j_data):
        """include the fixed data defined in configuration"""
        f_values = self.configuration.get_topic_data("bioinfo_analysis", "fixed_values")
        for row in j_data:
            for field, value in f_values.items():
                row[field] = value
        return j_data

    def include_data_from_mapping_stats(self, j_data):
        """By processing mapping stats file the following information is
        included in schema properties:  depth_of_coverage_value, lineage_name,
        number_of_variants_in_consensus, number_of_variants_with_effect,
        per_genome_greater_10x. per_Ns. per_reads_host, per_reads_virus.
        per_unmapped, qc_filtered, reference_genome_accession
        """
        # position of the sample columns inside mapping file
        sample_position = 4
        map_data = relecov_tools.utils.read_csv_file_return_dict(
            self.req_files["mapping_stats"], "\t", sample_position
        )

        mapping_fields = self.configuration.get_topic_data(
            "bioinfo_analysis", "mapping_stats"
        )

        for row in j_data:
            for field, value in mapping_fields.items():
                try:
                    row[field] = map_data[row["sequencing_sample_id"]][value]
                except KeyError as e:
                    log.error("Field %s not found in mapping stats", e)
                    stderr.print(f"[red]Field {e} not found in mapping stats")
                    sys.exit(1)
        return j_data

    def include_pangolin_data(self, j_data):
        """Include pangolin data collecting form each file generated by pangolin"""
        mapping_fields = self.configuration.get_topic_data(
            "bioinfo_analysis", "mapping_pangolin"
        )
        for row in j_data:
            if "-" in row["sequencing_sample_id"]:
                sample_name = row["sequencing_sample_id"].replace("-", "_")
            else:
                sample_name = row["sequencing_sample_id"]
            f_name = sample_name + ".pangolin." + row["analysis_date"] + ".csv"
            f_path = os.path.join(self.input_folder, f_name)

            try:
                f_data = relecov_tools.utils.read_csv_file_return_dict(f_path, ",")
            except FileNotFoundError as e:
                log.error("File %s not found ", e)
                stderr.print(f"[red]File {e} not found")
                # When file does not exist set all values to empty
                for field, value in mapping_fields.items():
                    row[field] = ""
                continue
                # sys.exit(1)
            pang_key = list(f_data.keys())[0]
            for field, value in mapping_fields.items():
                row[field] = f_data[pang_key][value]

        return j_data

    def include_consensus_data(self, j_data):
        """Include genome length, name, file name, path and md5 by preprocessing
        each file of consensus.fa
        """

        mapping_fields = self.configuration.get_topic_data(
            "bioinfo_analysis", "mapping_consensus"
        )
        for row in j_data:
            if "-" in row["sequencing_sample_id"]:
                sample_name = row["sequencing_sample_id"].replace("-", "_")
            else:
                sample_name = row["sequencing_sample_id"]
            f_name = sample_name + ".consensus.fa"
            f_path = os.path.join(self.input_folder, f_name)
            try:
                record_fasta = relecov_tools.utils.read_fasta_return_SeqIO_instance(
                    f_path
                )
            except FileNotFoundError as e:
                log.error("File %s not found ", e)
                stderr.print(f"[red]File {e} not found")
                for item in mapping_fields:
                    row[item] = ""
                continue
            row["consensus_genome_length"] = str(len(record_fasta))
            row["consensus_sequence_name"] = record_fasta.description
            row["consensus_sequence_filepath"] = self.input_folder
            row["consensus_sequence_filename"] = f_name
            row["consensus_sequence_md5"] = relecov_tools.utils.calculate_md5(f_path)
            base_calculation = int(row["read_length"]) * len(
                record_fasta
            )
            if row["sequencing_sample_id"] != "":
                row["number_of_base_pairs_sequenced"] = str(base_calculation * 2)
            else:
                row["number_of_base_pairs_sequenced"] = str(base_calculation)

        return j_data

    def include_long_table_path(self, j_data):
        """Include the variant long table path by searchin the in input folder
        the file name that contains long_table.csv
        """
        condition = os.path.join(self.input_folder, "*long_table.csv")
        f_path = relecov_tools.utils.get_files_match_condition(condition)
        if len(f_path) == 0:
            long_table_path = ""
        else:
            long_table_path = f_path[0]
        for row in j_data:
            row["long_table_path"] = long_table_path
        return j_data

    def include_variant_metrics(self, j_data):
        """Include the # Ns per 100kb consensus from the summary variant
        metric file_exists
        """
        map_data = relecov_tools.utils.read_csv_file_return_dict(
            self.req_files["variants_metrics"], ","
        )
        mapping_fields = self.configuration.get_topic_data(
            "bioinfo_analysis", "mapping_variant_metrics"
        )
        for row in j_data:
            for field, value in mapping_fields.items():
                try:
                    row[field] = map_data[row["sequencing_sample_id"]][value]
                except KeyError as e:
                    log.error("Field %s not found in mapping stats", e)
                    stderr.print(f"[red]Field {e} not found in mapping stats")
                    sys.exit(1)
        return j_data

    def include_software_versions(self, j_data):
        """Include versions from the yaml version file"""
        version_fields = self.configuration.get_topic_data(
            "bioinfo_analysis", "mapping_version"
        )

        try:
            versions = relecov_tools.utils.read_yml_file(self.req_files["versions"])
        except YAMLError as e:
            log.error("Unable to process version file return error %s", e)
            stderr.print("[red]Unable to process version file")
            stderr.print(f" {e}")
            sys.exit(1)
        for row in j_data:
            for field, version_data in version_fields.items():

                for key, value in version_data.items():

                    row[field] = versions[key][value]
        return j_data

    def collect_info_from_lab_json(self):
        """Create the list of dictionaries from the data that is on json lab
        metadata file. Return j_data that is used to add the rest of the fields
        """
        try:
            json_lab_data = relecov_tools.utils.read_json_file(self.json_file)
        except ValueError:
            log.error("%s invalid json file", self.json_file)
            stderr.print(f"[red] {self.json_file} invalid json file")
            sys.exit(1)
        import pdb; pdb.set_trace()
#        j_data = []
#        mapping_fields = self.configuration.get_topic_data(
#            "bioinfo_analysis", "required_fields_from_lab_json"
#        )
#        for row in json_lab_data:
#            j_data_dict = {}
#            for lab_field, bio_field in mapping_fields.items():
#                j_data_dict[bio_field] = row[lab_field]
#            j_data.append(j_data_dict)
        return json_lab_data

    def create_bioinfo_file(self):
        """Create the bioinfodata json with collecting information from lab
        metadata json, mapping_stats, and more information from the files
        inside input directory
        """
        stderr.print("[blue]Reading lab metadata json")
        j_data = self.collect_info_from_lab_json()
        stderr.print("[blue]Adding fixed values")
        j_data = self.add_fixed_values(j_data)
        stderr.print("[blue]Adding data from mapping stats")
        j_data = self.include_data_from_mapping_stats(j_data)
        stderr.print("[blue]Adding software versions")
        j_data = self.include_software_versions(j_data)
        stderr.print("[blue]Adding summary variant metrics")
        j_data = self.include_variant_metrics(j_data)
        stderr.print("[blue]Adding pangolin informtion")
        j_data = self.include_pangolin_data(j_data)
        stderr.print("[blue]Adding consensus data")
        j_data = self.include_consensus_data(j_data)
        stderr.print("[blue]Adding variant long table path")
        j_data = self.include_long_table_path(j_data)
        file_name = (
            "bioinfo_" + os.path.splitext(os.path.basename(self.json_file))[0] + ".json"
        )
        stderr.print("[blue]Writting output json file")
        os.makedirs(self.output_folder, exist_ok=True)
        file_path = os.path.join(self.output_folder, file_name)
        relecov_tools.utils.write_json_fo_file(j_data, file_path)
        stderr.print("[green]Sucessful creation of bioinfo analyis file")
        return True
